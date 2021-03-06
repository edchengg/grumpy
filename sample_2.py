from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import time
import os


import numpy as np
import tensorflow as tf
import reader_1
import os

import hm_rnn

import sample_1

FLAGS = tf.flags.FLAGS

#tf.flags.DEFINE_string("save_path", ".", "The directory of the model file to evaluate.")

#tf.flags.DEFINE_string("data_path", ".", "The directory of the model file to evaluate.")



def data_type():
  return tf.float16 if FLAGS.use_fp16 else tf.float32


class PTBInput(object):
	"""The input data."""

	def __init__(self, config, data, name=None):
		self.batch_size = batch_size = config.batch_size
		self.num_steps = num_steps = config.num_steps
		self.epoch_size = ((len(data) // batch_size) - 1) // num_steps
		self.input_data, self.targets = reader_1.ptb_producer(data, batch_size, num_steps, name=name)





class SmallGenConfig(object):
	"""Small config. for generation"""
	init_scale = 0.1
	learning_rate = 0.7
	max_grad_norm = 5
	num_layers = 2
	num_steps = 1 # this is the main difference
	hidden_size = 200
	max_epoch = 5
	max_max_epoch = 20
	keep_prob = 1.0
	lr_decay = 0.5
	batch_size = 1
	vocab_size = 10000



def generate_text(train_path, model_path, num_sentences):
	
	gen_config = SmallGenConfig()
	
	print ("model_path ---> ", model_path)

	checkpoint_file = tf.train.latest_checkpoint(model_path)

	with  tf.Graph().as_default(),tf.Session() as session: # tf.Graph().as_default(),


		#Method 1:

		#tf.initialize_all_variables().run()
		#saver = tf.train.Saver()
		#ckpt = tf.train.get_checkpoint_state(checkpoint_file)

		#if ckpt and ckpt.model_checkpoint_path:
			#saver.restore(session, ckpt.model_checkpoint_path)
		#print ("Session restored successfully!")


		# Method 2:

		#print('loading checkpoint from from {}'.format(checkpoint_file))
		#saver = tf.train.import_meta_graph("{}.meta".format(checkpoint_file))
		#saver.restore(session, checkpoint_file)




		print("FLAGS.data_path -->",FLAGS.data_path)
		raw_data = reader_1.ptb_raw_data(FLAGS.data_path)
		train_data, valid_data, test_data, word_to_id, id_2_word = raw_data

		print("FLAGS.save_path -->",FLAGS.save_path)
		checkpoint_file = tf.train.latest_checkpoint(FLAGS.save_path)



		initializer = tf.random_uniform_initializer(-gen_config.init_scale,gen_config.init_scale)    

		with tf.variable_scope("model", reuse=None, initializer=initializer):
			train_input = PTBInput(config=gen_config, data=train_data, name="TrainInput")
			m = sample_1.PTBModel(is_training=False, config=gen_config, input_=train_input)

		print ('Model Variables:')
		print ( print([var.name for var in tf.all_variables()]) )

		# Restore variables from disk.
		saver = tf.train.Saver() 
		print ("Restoring from : ", model_path)
		saver.restore(session, model_path)


		
		#words = reader_1.get_vocab(train_path)

		state = m.initial_state.eval()
		x = 2 # the id for '<eos>' from the training set
		input = np.matrix([[x]])  # a 2D numpy matrix 

		text = ""
		count = 0
		while count < num_words:
			output_probs, state = session.run([m.output_probs, m.final_state],
																	 {m.input_data: input,
																		m.initial_state: state})
			x = sample(output_probs[0], 0.9)
			
			#text += " " + words[x]

			text += " " + id_2_word[x]
			count += 1

			# now feed this new word as input into the next iteration
			input = np.matrix([[x]])

		print(text)
	return


def main():


	#train_path = "/Users/SeansMBP/Desktop/Cho/Project/data/tagged/train.txt"

	#model_path = "/Users/SeansMBP/Desktop/Cho/Project/git stuff/grumpy/save_dir/tagged_hmlstm_small_drop_1213235534"

	train_path = FLAGS.data_path

	model_path = FLAGS.save_path

	num_words = 360

	generate_text(train_path, model_path, num_words)





if __name__ == '__main__':
	main()
