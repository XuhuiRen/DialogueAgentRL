CUDA_VISIBLE_DEVICES=1 python run.py --agt 11 --usr 1 --max_turn 40 \
	      --movie_kb_path ./deep_dialog/data/movie_kb.1k.p \
	      --dqn_hidden_size 80 \
	      --experience_replay_pool_size 1000 \
	      --episodes 500 \
	      --simulation_epoch_size 100 \
	      --write_model_dir ./deep_dialog/checkpoints/boltzmann_agent/ \
	      --run_mode 3 \
	      --slot_err_prob 0.00 \
	      --intent_err_prob 0.00 \
	      --batch_size 16 \
	      --goal_file_path ./deep_dialog/data/user_goals_first_turn_template.part.movie.v1.p \
	      --warm_start 2 \
	      --warm_start_epochs 0 \
              --final_checkpoint_path ./deep_dialog/checkpoints/boltzmann_agent/agt_11_170_280_0.91000.p \
	      --act_level 1 \
              --test_mode \
              --cmd_input_mode 0 \

