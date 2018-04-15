
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
from keras.models import Sequential,Model
from keras.layers import Dense,Input,Lambda
from keras.optimizers import Adam
import ipdb

import gym

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

sess = tf.Session(config=tf.ConfigProto(log_device_placement=True,
    allow_soft_placement=True))
keras.backend.set_session(sess)

def one_hot(action, categories=4):
    x = np.zeros(categories)
    x[action] = 1
    return x

class AgentA2C(Agent):
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
	#epsilon:
	self.params = params
	self.epsilon = params['epsilon']
	#
	self.agent_run_mode = params['agent_run_mode']
	#
	self.agent_act_level = params['agent_act_level']
	# experience replay
	#self.experience_replay_pool_size = params.get('experience_replay_pool_size', 1000)
	#self.experience_replay_pool = [] #Replay_Memory(self.experience_replay_pool_size)
	self.hidden_size = params.get('dqn_hidden_size', 60)
	# gamma : discount factor
	self.gamma = params.get('gamma', 0.99)
	self.predict_mode = params.get('predict_mode', False)
	self.actor_lr = params.get('actor_lr', 0.0005)
	self.critic_lr = params.get('critic_lr', 0.0005)
        ## warm start:
	#self.warm_start = params.get('warm_start', 0)

	self.max_turn = params['max_turn'] + 4
	self.state_dimension = 2 * self.act_cardinality + 7 * self.slot_cardinality + 3 + self.max_turn
        
        # Build models
        self.build_actor_model(self.actor_lr)
        self.build_critic_model(self.critic_lr)
        self.n = params.get('n', 50)
        
        ## load a model if present
        if params['trained_model_path'] != None:
            self.actor_model = copy.deepcopy(self.load(
                params['trained_actor_model_path']))
            self.critic_model = copy.deepcopy(self.load(
                params['trained_actor_model_path']))
            self.predict_mode = True
            self.warm_start = 2
    
    def load(self, name, model_name):
        if model_name=="actor":
            self.actor_model.load(name)
        else:
            self.critic_model.load(name)
    
    def save(self, name, model_name):
        if model_name=="actor":
            self.actor_model.save_weights(name)
        else:
            self.critic_model.save_weights(name)



    def load_actor_model(self, model_config_path,lr):
        with open(model_config_path, 'r') as f:
            model = keras.models.model_from_json(f.read())
        model.compile(loss='categorical_crossentropy',optimizer=Adam(lr=lr))
        self.actor_model = model
    
    def build_actor_model(self, actor_lr):
        model = Sequential()
        fc1 = Dense(50, input_shape=(self.state_dimension,),activation='relu',
                kernel_initializer=VarianceScaling(mode='fan_avg',
                    distribution='normal'))
        fc2 = Dense(50, activation='relu',
                kernel_initializer=VarianceScaling(mode='fan_avg',
                    distribution='normal'))

        fc3 = Dense(self.num_actions, activation='linear',
                kernel_initializer=VarianceScaling(mode='fan_avg',
                    distribution='normal'))
        model.add(fc1)
        model.add(fc2)
        model.add(fc3)
        model.compile(loss='mse',optimizer=Adam(lr=self.actor_lr))
        self.actor_model = model


    def build_critic_model(self,critic_lr):
        model = Sequential()
        fc1 = Dense(50, input_shape=(self.state_dimension,),activation='relu',
                kernel_initializer=VarianceScaling(mode='fan_avg',
                    distribution='normal'))
        fc2 = Dense(50, activation='relu',
                kernel_initializer=VarianceScaling(mode='fan_avg',
                    distribution='normal'))
        fc3 = Dense(1, activation='relu',
                kernel_initializer=VarianceScaling(mode='fan_avg',
                    distribution='normal'))
        model.add(fc1)
        model.add(fc2)
        model.add(fc3)
        model.compile(loss='mse',optimizer=Adam(lr=self.critic_lr))
        self.critic_model = model
    
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
        self.representation = self.prepare_state_representation(state)
        batch_size = self.representation.shape[0]
        #self.action = self.run_policy(self.representation)
        try:
            self.action = self.actor_model.predict_on_batch(
                    self.representation.reshape(1, batch_size))
        except:
            ipdb.set_trace()
        act_slot_response = copy.deepcopy(
                self.feasible_actions[np.argmax(self.action[0])])
        return {'act_slot_response': act_slot_response, 'act_slot_value_response': None}, self.action[0]

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
        action =  qvalues.data.max(1)[1]
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
                # return self.dqn.predict(representation, {}, predict_model=True)
                return self.return_greedy_action(representation)
   
    def get_advantage(self, states, rewards):
        T = len(rewards)
        v_end = np.zeros(T)
        gain = np.zeros(T)
        advantage = np.zeros(T)
        #states = [self.prepare_state_representation(x) for x in states]
        for t in reversed(range(len(rewards)-1)):
            if t + self.n >= T:
                v_end[t] = 0
            else:
                v_end[t] = self.critic_model.predict(
                        np.asarray([states[t + self.n]]))[0]
            gain[t] = self.gamma**self.n * v_end[t] + \
                    sum([(self.gamma**k) * rewards[t+k] \
                        if t+k < T \
                        else self.gamma**k * 0 \
                        for k in range(self.n)])
            advantage[t] = gain[t] - self.critic_model.predict(np.asarray(
                [states[t]]))[0]
        return advantage
  
    def train(self, states, actions, rewards, gamma=0.99):
        states = [self.prepare_state_representation(x) for x in states]
        advantage = self.get_advantage(states, rewards)

        #ipdb.set_trace()
        advantage = advantage.reshape(-1,1)
        actions = np.asarray(actions)

        # L(\theta) from the handout
        targets = advantage*actions
        states = np.asarray(states)
        rewards = np.asarray(rewards)
        tot_rewards = np.sum(rewards)

        self.actor_model.train_on_batch(states, targets)
        self.critic_model.train_on_batch(states, rewards)
        return tot_rewards

    def evaluate(self,env,episode,num_episodes=100,render=False):

        cumulative_rewards = []
        for e in range(num_episodes):
            state = env.reset()
            tot_reward = 0
            while True:
                action_probs = self.actor_model.predict(np.asarray([state]))
                action = np.random.choice(np.arange(
                    len(action_probs[0])),p=action_probs[0])
                state, reward, done, _ = env.step(action)
                tot_reward += reward
                if done:
                    break
            cumulative_rewards.append(tot_reward)
        mean_rewards = np.mean(cumulative_rewards)
        std_rewards = np.std(cumulative_rewards)
        return mean_rewards,std_rewards
