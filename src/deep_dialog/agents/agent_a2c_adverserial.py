from deep_dialog import dialog_config
from collections import deque
from agent import Agent
## so you can remove extreeous agent information in the dqn-pytorch file
import os
import sys
import argparse
import numpy as np
import tensorflow as tf
from datetime import datetime
import json, copy
import logging
import keras
from keras.initializers import VarianceScaling
from keras.models import Sequential, Model
from keras import regularizers
from keras.layers import Dense, Input, Lambda
from keras.optimizers import Adam
from constants import *
import random
import ipdb
from deep_dialog.qlearning import DQN
import gym
from deep_dialog.dialog_system import DialogManager, text_to_dict
import matplotlib
from deep_dialog.usersims import RuleSimulator
matplotlib.use('Agg')
import matplotlib.pyplot as plt

sess = tf.Session(config=tf.ConfigProto(log_device_placement=True,allow_soft_placement=True))
keras.backend.set_session(sess)


def one_hot(action, categories=4):
	x = np.zeros(categories)
	x[action] = 1
	return x


class AgentAdverserialA2C(Agent):
	def __init__(self, movie_dict=None, act_set=None, slot_set=None, params=None):

		## parameters associated with dialogue action and slot filling
		self.movie_dict = movie_dict
		self.act_set = act_set
		self.slot_set = slot_set
		self.act_cardinality = len(act_set.keys())
		self.slot_cardinality = len(slot_set.keys())

		self.feasible_actions = dialog_config.feasible_actions
		self.num_actions = len(self.feasible_actions)

		# rl specific parameters
		# epsilon:
		self.params = params
		self.epsilon = params['epsilon']
		#
		self.agent_run_mode = params['agent_run_mode']
		#
		self.agent_act_level = params['agent_act_level']
		# experience replay
		# self.experience_replay_pool_size = params.get('experience_replay_pool_size', 1000)
		# self.experience_replay_pool = [] #Replay_Memory(self.experience_replay_pool_size)
		self.hidden_size = params.get('dqn_hidden_size', 60)
		# gamma : discount factor
		self.gamma = params.get('gamma', 0.99)
		self.predict_mode = params.get('predict_mode', False)
		self.actor_lr = params.get('actor_lr', 0.0005)
		self.critic_lr = params.get('critic_lr', 0.001)
		self.gan_critic_lr = params.get('gan_critic_lr', 0.001)
		self.discriminator_lr = params.get('discriminator_lr', 0.0005)
		self.discriminator_batch_size = params.get('discriminator_batch_size', 1)
		self.expert_path = params["expert_path"]
                self.reg_cost = self.params.get('reg_cost', 1e-3)

		## warm start:
		## there is no warm start since there are is no experience replay
		# self.warm_start = params.get('warm_start', 0)

		self.max_turn = params['max_turn'] + 4
		self.state_dimension = 2 * self.act_cardinality + 7 * self.slot_cardinality + 3 + self.max_turn
                self.expert_weights = params['expert_weights']
		# Build models
		self.build_actor_model(self.actor_lr)
		self.build_critic_model(self.critic_lr)
		self.build_critic_model(self.gan_critic_lr, True)
		self.build_discriminator(self.gan_critic_lr)
		self.n = params.get('n', 50)

		## load a model if present
		if params['trained_model_path'] != None:
			self.load(params['trained_actor_model_path'], "actor")
			self.load(params['trained_critic_model_path'], "critic")
			self.load(params['trained_adversarial_critic_model_path'], "advesarial_critic")
			self.load(params['trained_discriminator_model_path'], "discriminator")
			self.predict_mode = True
			self.warm_start = 2
		#self.expert = DQN(self.state_dimension, self.hidden_size, self.hidden_size, self.num_actions)
		self.expert = self.build_expert_model()
		# self.clone_dqn = copy.deepcopy(self.expert)
		# self.clone_dqn = keras.models.clone_model(self.expert)
		self.cur_bellman_err = 0
			
		# Prediction Mode: load trained DQN model
                if params['expert_path'] != None:
		    # self.dqn.model = model_from_json(params['expert_path'])
                    # copy.deepcopy(self.load_trained_DQN(params['expert_path']))
		    # self.dqn.model.load_weights(params['expert_weights'])
		    self.predict_mode = True
		    self.warm_start = 2
                user_sim = RuleSimulator(params['movie_dictionary'], 
                        params['act_set'], 
                        params['slot_set'], 
                        params['goal_set'], 
                        params['usersim_params'])
                self.dialog_manager = DialogManager(self.expert, 
                        user_sim, 
                        params['act_set'], 
                        params['slot_set'], 
                        params['movie_kb'])
                user_sim.set_nlg_model(params['nlg'])
                user_sim.set_nlu_model(params['nlu'])

	def load(self, name, model_name):
		if model_name == "actor":
			self.actor_model.load(name)
		elif model_name == "critic":
			self.critic_model.load(name)
		elif model_name == "advesarial_critic":
			self.adversarial_critic_model.load(name)
		elif model_name == "discriminator":
			self.discriminator.load(name)

	def save(self, name, model_name):
		if model_name == "actor":
			self.actor_model.save_weights(name)
		elif model_name == "critic":
			self.critic_model.save_weights(name)
		elif model_name == "advesarial_critic":
			self.adversarial_critic_model.save_weights(name)
		elif model_name == "discriminator":
			self.discriminator.save_weights(name)
			self.critic_model.save_weights(name)

	def load_actor_model(self, model_config_path, lr):
		with open(model_config_path, 'r') as f:
			model = keras.models.model_from_json(f.read())
		model.compile(loss='categorical_crossentropy', optimizer=Adam(lr=lr))
		self.actor_model = model

	def build_expert_model(self):
                model = Sequential()
		fc1 = Dense(self.hidden_size, input_shape=(self.state_dimension,), activation='relu',
					kernel_initializer=VarianceScaling(mode='fan_avg', distribution='normal'),
					kernel_regularizer=regularizers.l2(self.reg_cost))
		fc2 = Dense(self.hidden_size, activation='relu',
					kernel_initializer=VarianceScaling(mode='fan_avg', distribution='normal'),
					kernel_regularizer=regularizers.l2(self.reg_cost))

		fc3 = Dense(self.num_actions, activation='linear',
					kernel_initializer=VarianceScaling(mode='fan_avg', distribution='normal'),
					kernel_regularizer=regularizers.l2(self.reg_cost))
		model.add(fc1)
		model.add(fc2)
		model.add(fc3)
                #self.expert.model_from_json(self.expert_path)
                model.load_weights(self.expert_weights)
                return model
        
	def load_trained_DQN(self, path):
	    """ Load the trained DQN from a file """
	    
	    #trained_file = pickle.load(open(path, 'rb'))
	    #model = trained_file['model']
	    model = self.expert.load_weights(path)
	    print "trained DQN Parameters:", json.dumps(trained_file['params'], indent=2)
	    return model 
	
        def build_actor_model(self, actor_lr):
		model = Sequential()
		fc1 = Dense(50, input_shape=(self.state_dimension,), 
			activation='relu',
			kernel_initializer=VarianceScaling(mode='fan_avg',
			distribution='normal'), kernel_regularizer=regularizers.l2(0.01))
		fc2 = Dense(50, activation='relu',
			kernel_initializer=VarianceScaling(mode='fan_avg',
			distribution='normal'), kernel_regularizer=regularizers.l2(0.01))
		fc3 = Dense(self.num_actions, activation='softmax',
			kernel_initializer=VarianceScaling(mode='fan_avg',
			distribution='normal'), kernel_regularizer=regularizers.l2(0.01))
		model.add(fc1)
		model.add(fc2)
		model.add(fc3)
		model.compile(loss='mse', optimizer=Adam(lr=self.actor_lr))
		self.actor_model = model

	def build_critic_model(self, critic_lr, is_adverserial = False):
		model = Sequential()
		fc1 = Dense(50, input_shape=(self.state_dimension,), 
                        activation='relu',
			kernel_initializer=VarianceScaling(mode='fan_avg',
			distribution='normal'), 
                        kernel_regularizer=regularizers.l2(0.01))
		fc2 = Dense(50, activation='relu',
			kernel_initializer=VarianceScaling(mode='fan_avg',
			distribution='normal'), 
                        kernel_regularizer=regularizers.l2(0.01))
		fc3 = Dense(1, activation='relu',
			kernel_initializer=VarianceScaling(mode='fan_avg',
			distribution='normal'), 
                        kernel_regularizer=regularizers.l2(0.01))
		model.add(fc1)
		model.add(fc2)
		model.add(fc3)
		model.compile(loss='mse', optimizer=Adam(lr=self.critic_lr))
		if is_adverserial:
			self.adversarial_critic_model = model
		else:
			self.critic_model = model

	def build_discriminator(self, discriminator_lr):
		model = Sequential()
		fc1 = Dense(50, input_shape=(self.state_dimension + self.num_actions ,), activation='relu',
					kernel_initializer=VarianceScaling(mode='fan_avg',
													   distribution='normal'))
		fc2 = Dense(50, activation='relu',
					kernel_initializer=VarianceScaling(mode='fan_avg',
													   distribution='normal'))
		fc3 = Dense(1, activation='sigmoid',
					kernel_initializer=VarianceScaling(mode='fan_avg',
													   distribution='normal'))
		model.add(fc1)
		model.add(fc2)
		model.add(fc3)
		model.compile(optimizer=Adam(lr=self.discriminator_lr) , loss='binary_crossentropy', metrics=['accuracy'])
		self.discriminator = model

	def initialize_episode(self):
		""" Initialize a new episode. This function is called every time a new episode is run. """

		self.current_slot_id = 0
		self.phase = 0
		self.request_set = ['moviename', 'starttime', 'city', 'date', 'theater', 'numberofpeople']

	def prepare_state_representation(self, state):
		""" Create the representation for each state """

		user_action = state['user_action']
		current_slots = state['current_slots']
		kb_results_dict = state['kb_results_dict']
		agent_last = state['agent_action']

		########################################################################
		#   Create one-hot of acts to represent the current user action
		########################################################################
		user_act_rep = np.zeros((1, self.act_cardinality))
		user_act_rep[0, self.act_set[user_action['diaact']]] = 1.0

		########################################################################
		#     Create bag of inform slots representation to represent the current user action
		########################################################################
		user_inform_slots_rep = np.zeros((1, self.slot_cardinality))
		for slot in user_action['inform_slots'].keys():
			user_inform_slots_rep[0, self.slot_set[slot]] = 1.0

		########################################################################
		#   Create bag of request slots representation to represent the current user action
		########################################################################
		user_request_slots_rep = np.zeros((1, self.slot_cardinality))
		for slot in user_action['request_slots'].keys():
			user_request_slots_rep[0, self.slot_set[slot]] = 1.0

		########################################################################
		#   Creat bag of filled_in slots based on the current_slots
		########################################################################
		current_slots_rep = np.zeros((1, self.slot_cardinality))
		for slot in current_slots['inform_slots']:
			current_slots_rep[0, self.slot_set[slot]] = 1.0

		########################################################################
		#   Encode last agent act
		########################################################################
		agent_act_rep = np.zeros((1, self.act_cardinality))
		if agent_last:
			agent_act_rep[0, self.act_set[agent_last['diaact']]] = 1.0

		########################################################################
		#   Encode last agent inform slots
		########################################################################
		agent_inform_slots_rep = np.zeros((1, self.slot_cardinality))
		if agent_last:
			for slot in agent_last['inform_slots'].keys():
				agent_inform_slots_rep[0, self.slot_set[slot]] = 1.0

		########################################################################
		#   Encode last agent request slots
		########################################################################
		agent_request_slots_rep = np.zeros((1, self.slot_cardinality))
		if agent_last:
			for slot in agent_last['request_slots'].keys():
				agent_request_slots_rep[0, self.slot_set[slot]] = 1.0

		turn_rep = np.zeros((1, 1)) + state['turn'] / 10.

		########################################################################
		#  One-hot representation of the turn count?
		########################################################################
		turn_onehot_rep = np.zeros((1, self.max_turn))
		turn_onehot_rep[0, state['turn']] = 1.0

		########################################################################
		#   Representation of KB results (scaled counts)
		########################################################################
		kb_count_rep = np.zeros((1, self.slot_cardinality + 1)) + kb_results_dict['matching_all_constraints'] / 100.
		for slot in kb_results_dict:
			if slot in self.slot_set:
				kb_count_rep[0, self.slot_set[slot]] = kb_results_dict[slot] / 100.

		########################################################################
		#   Representation of KB results (binary)
		########################################################################
		kb_binary_rep = np.zeros((1, self.slot_cardinality + 1)) + np.sum(
			kb_results_dict['matching_all_constraints'] > 0.)
		for slot in kb_results_dict:
			if slot in self.slot_set:
				kb_binary_rep[0, self.slot_set[slot]] = np.sum(kb_results_dict[slot] > 0.)

		self.final_representation = np.hstack(
			[user_act_rep, user_inform_slots_rep, user_request_slots_rep, agent_act_rep, agent_inform_slots_rep,
			 agent_request_slots_rep, current_slots_rep, turn_rep, turn_onehot_rep, kb_binary_rep, kb_count_rep])
		self.final_representation = np.squeeze(self.final_representation)
		return self.final_representation

	def state_to_action(self, state):
		""" A2C: Input state, output action """
		## Dialogue manager calls this to fill the experience buffer ##
		## TODO: Fix this for A2C return multinomial output
		self.representation = self.prepare_state_representation(state)
		batch_size = self.representation.shape[0]
		# self.action = self.run_policy(self.representation)
		try:
			self.action = self.actor_model.predict_on_batch(
				self.representation.reshape(1, batch_size))
		except:
			ipdb.set_trace()
                self.action = self.action.squeeze(0)
                idx = np.random.choice(self.num_actions, 1, p=self.action)[0]
		act_slot_response = copy.deepcopy(self.feasible_actions[idx])
                return {'act_slot_response': act_slot_response, 'act_slot_value_response': None}, idx, self.action[idx]

	def rule_policy(self):
		""" Rule Policy """

		if self.current_slot_id < len(self.request_set):
			slot = self.request_set[self.current_slot_id]
			self.current_slot_id += 1

			act_slot_response = {}
			act_slot_response['diaact'] = "request"
			act_slot_response['inform_slots'] = {}
			act_slot_response['request_slots'] = {slot: "UNK"}
		elif self.phase == 0:
			act_slot_response = {'diaact': "inform", 'inform_slots': {'taskcomplete': "PLACEHOLDER"},
								 'request_slots': {}}
			self.phase += 1
		elif self.phase == 1:
			act_slot_response = {'diaact': "thanks", 'inform_slots': {}, 'request_slots': {}}

		return self.action_index(act_slot_response)

	def action_index(self, act_slot_response):
		""" Return the index of action """

		for (i, action) in enumerate(self.feasible_actions):
			if act_slot_response == action:
				return i
		print act_slot_response
		raise Exception("action index not found")
		return None

	def return_greedy_action(self, state_representation):
		# TODO: Fix this A2C
		state_var = variable(torch.FloatTensor(state_representation).unsqueeze(0))
		if torch.cuda.is_available():
			state_var = state_var.cuda()
		qvalues = self.actor_model.predict(np.asarray(state_var))
		action = qvalues.data.max(1)[1]
		return action[0]

	def run_policy(self, representation):
		""" epsilon-greedy policy """
		# TODO: Remove this for A2C
		if random.random() < self.epsilon:
			return random.randint(0, self.num_actions - 1)
		else:
			if self.warm_start == 1:
				## if in training mode(not prediction) fill until you cant anymore
				if len(self.experience_replay_pool) > self.experience_replay_pool_size:
					self.warm_start = 2
				return self.rule_policy()
			else:
				# return self.expert.predict(representation, {}, predict_model=True)
				return self.return_greedy_action(representation)

	def get_advantage(self, states, rewards, is_adversary = False):
		T = len(rewards)
		v_end = np.zeros(T)
		gain = np.zeros(T)
		advantage = np.zeros(T)
		# states = [self.prepare_state_representation(x) for x in states]
		for t in reversed(range(len(rewards) - 1)):
			if t + self.n >= T:
				v_end[t] = 0
			else:
				if is_adversary:
					v_end[t] = self.adversarial_critic_model.predict(
						np.asarray([states[t + self.n]]))[0]
				else:
					v_end[t] = self.critic_model.predict(
						np.asarray([states[t + self.n]]))[0]
			gain[t] = self.gamma ** self.n * v_end[t] + \
					  sum([(self.gamma ** k) * rewards[t + k] \
							   if t + k < T \
							   else self.gamma ** k * 0 \
						   for k in range(self.n)])
			if is_adversary:
				advantage[t] = gain[t] - self.adversarial_critic_model.predict(np.asarray(
					[states[t]]))[0]
			else:
				advantage[t] = gain[t] - self.critic_model.predict(np.asarray(
					[states[t]]))[0]
		return advantage, gain

	def generate_expert_episode(self):
		## TODO: Initialize expert policy as the policy that takes epsilon greedy actions in DQN trained agent
                states = []
                actions = []
                rewards = []
                done = False
                self.dialog_manager.initialize_episode()
                while(not done):
                    temp, idx, act = self.dialog_manager.next_turn()
                    episode_over = temp[0]
                    cumulative_reward += reward
		    if episode_over:
                        if reward > 0: 
                            successes += 1
                            print ("Expert episode %s: Success" % (episode))
                        else: print ("Expert episode %s: Fail" % (episode))
                        #cumulative_turns += dialog_manager.state_tracker.turn_count
                        #cumulative_turn_list.append(dialog_manager.state_tracker.turn_count)
                        #cumulative_reward_list.append(episode_reward)
                    states.append(dialog_manager.state)
                    rewards.append(reward)
                    actions.append(act)
                #indexes.append(idx)
                return states, actions, rewards

	def train(self, states, actions, rewards, indexes, gamma=0.99):
		states = [self.prepare_state_representation(x) for x in states]
		advantage, gains = self.get_advantage(states, rewards)

		advantage = advantage.reshape(-1, 1)
		actions = np.asarray(actions)

		# L(\theta) from the handout
                #act_target[np.arange(len(states)), np.array(actions)] \
                #        = (np.array(discounted_rewards) - np.array(values))
                targets = advantage #* actions
		act_target = np.zeros((len(states),self.num_actions))
                act_target[np.arange(len(states)), np.array(indexes)] \
                                    = targets.squeeze(1)
		states = np.asarray(states)
                #TODO: Check if we want to scale rewards
                rewards = np.asarray(rewards)
		tot_rewards = np.sum(rewards)

		self.actor_model.train_on_batch(states, act_target)
		self.critic_model.train_on_batch(states, gains)

		## sample from an expert episode and the current simulated episode
		## in Goodfellow's original paper, he does it k times
		## TODO - Done: Adversarial expert episide will be generated by the best DQN episodes
                expert_states, expert_actions = self.generate_expert_episode()
		sampled_expert_index = np.random.randint(0, len(expert_states))
		one_hot_expert_action = np.zeros((1, self.num_actions))
		one_hot_expert_action[:, expert_actions[sampled_expert_index]] = 1
		sampled_expert_state = np.array(expert_states[sampled_expert_index])
		sampled_expert_state = np.expand_dims(sampled_expert_state, 0)
		sampled_expert_example = np.concatenate((sampled_expert_state, one_hot_expert_action), axis=1)
		sampled_simulated_index = np.random.randint(0, len(states))
		one_hot_simulated_action = np.zeros((1, self.num_actions))
		one_hot_simulated_action[:, actions[sampled_simulated_index]] = 1
		sampled_simulated_state = states[sampled_simulated_index]
		sampled_simulated_state = np.expand_dims(sampled_simulated_state, 0)
		sampled_simulated_example = np.concatenate((sampled_simulated_state, one_hot_simulated_action), axis=1)

		## train discriminator
		d_loss_real = self.discriminator.train_on_batch(sampled_expert_example,
														np.ones((self.discriminator_batch_size, 1)))
		d_loss_fake = self.discriminator.train_on_batch(sampled_simulated_example,
														np.zeros((self.discriminator_batch_size, 1)))
		d_loss = 0.5 * np.add(d_loss_real, d_loss_fake)

		## compute gan rewards
		## call predict on a batch of the current simulated  episodes to get the class value
		state_action_pairs = []
		for s, a in zip(states, actions):
			one_hot = np.zeros(self.num_actions)
			one_hot[a] = 1
			concat_s_a = np.concatenate((s, one_hot))
			state_action_pairs.append(concat_s_a)
		probability_simulation = self.discriminator.predict(np.array(state_action_pairs))
		gan_rewards = (-np.log(1 - probability_simulation)).flatten().tolist()

		''' Train gan actor-critic network '''
		gan_values = self.compute_baseline(states, isgan=True)
		gan_discounted_rewards = self.get_value_reward(states, gan_rewards, gan_values)
		gan_act_target = np.zeros((len(states), self.num_actions))
		gan_act_target[np.arange(len(states)), np.array(actions)] = (np.array(gan_discounted_rewards)
																	 - np.array(gan_values))
		gan_critic_target = np.array(gan_discounted_rewards)
		gan_actor_loss = self.actor_model.train_on_batch(states, gan_act_target)
		gan_critic_loss = self.adversarial_critic_model.train_on_batch(states, gan_critic_target)

		advantage, gains = self.get_advantage(states, rewards, True)
		targets = advantage #* actions
		act_target = np.zeros((len(states),self.num_actions))
                act_target[np.arange(len(states)), np.array(indexes)] \
                                    = targets.squeeze(1)

		self.actor_model.train_on_batch(states, act_target)
		self.critic_model.train_on_batch(states, gains)

		return tot_rewards

	def evaluate(self, env, episode, num_episodes=100, render=False):

		cumulative_rewards = []
		for e in range(num_episodes):
			state = env.reset()
			tot_reward = 0
			while True:
				action_probs = self.actor_model.predict(np.asarray([state]))
				action = np.random.choice(np.arange(
					len(action_probs[0])), p=action_probs[0])
				state, reward, done, _ = env.step(action)
				tot_reward += reward
				if done:
					break
			cumulative_rewards.append(tot_reward)
		mean_rewards = np.mean(cumulative_rewards)
		std_rewards = np.std(cumulative_rewards)
		return mean_rewards, std_rewards
