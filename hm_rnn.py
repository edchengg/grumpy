
# Note: Requires Tensorflow 0.11 or lower. Breaks in Tensorflow 0.12.

from enum import Enum
import collections
import tensorflow as tf
from tensorflow.python.ops import variable_scope as vs
from tensorflow.python.framework import ops


# TODO start s2st code        
class StochasticGradientEstimator(Enum):
    ST = 0
    REINFORCE = 1

def binaryRound(x):
    """
    Rounds a tensor whose values are in [0,1] to a tensor with values in {0, 1}, 
    using the straight through estimator for the gradient.
    
    E.g.,:
    If x is >= 0.5, binaryRound(x) will be 1 and the gradient will be pass-through,
    otherwise, binaryRound(x) will be 0 and the gradient will be 0.
    """
    g = tf.get_default_graph()
    
    with ops.name_scope("BinaryRound") as name:
        # override "Floor" because tf.round uses tf.floor
        with g.gradient_override_map({"Floor": "BinaryRound"}):
            return tf.round(x, name=name)
        
@ops.RegisterGradient("BinaryRound")
def _binaryRound(op, grad):
    """Straight through estimator for the binaryRound op (identity if 1, else 0)."""
    x = op.outputs[0]
    return x * grad

def bernoulliSample(x):
    """
    Uses a tensor whose values are in [0,1] to sample a tensor with values in {0, 1},
    using the straight through estimator for the gradient.
    
    E.g.,:
    if x is 0.6, bernoulliSample(x) will be 1 with probability 0.6, and 0 otherwise,
    and the gradient will be pass-through (1) wih probability 0.6, and 0 otherwise. 
    """
    g = tf.get_default_graph()
    
    with ops.name_scope("BernoulliSample") as name:
        with g.gradient_override_map({"Ceil": "Identity","Sub": "BernoulliSample_ST"}):
            return tf.ceil(x - tf.random_uniform(tf.shape(x)), name=name)
        
@ops.RegisterGradient("BernoulliSample_ST")
def bernoulliSample_ST(op, grad):
    """Straight through estimator for the bernoulliSample op (identity if 1, else 0)."""
    sub = op.outputs[0] # x - tf.random_uniform... 
    res = sub.consumers()[0].outputs[0] # tf.ceil(sub)
    return [res * grad, tf.zeros(tf.shape(op.inputs[1]))]

def passThroughSigmoid(x, slope=1):
    """Sigmoid that uses identity function as its gradient"""
    g = tf.get_default_graph()
    with ops.name_scope("PassThroughSigmoid") as name:
        with g.gradient_override_map({"Sigmoid": "Identity"}):
            return tf.sigmoid(x, name=name)
        
def binaryStochastic_ST(x, slope_tensor=None, pass_through=True, stochastic=True):
    """
    Sigmoid followed by either a random sample from a bernoulli distribution according 
    to the result (binary stochastic neuron) (default), or a sigmoid followed by a binary
    step function (if stochastic == False). Uses the straight through estimator. 
    See https://arxiv.org/abs/1308.3432.
    
    Arguments:
    * x: the pre-activation / logit tensor
    * slope_tensor: if passThrough==False, slope adjusts the slope of the sigmoid function 
        for purposes of the Slope Annealing Trick (see http://arxiv.org/abs/1609.01704)
    * pass_through: if True (default), gradient of the entire function is 1 or 0; 
        if False, gradient of 1 is scaled by the gradient of the sigmoid (required if
        Slope Annealing Trick is used)
    * stochastic: binary stochastic neuron if True (default), or step function if False
    """
    if slope_tensor is None:
        slope_tensor = tf.constant(1.0)
        
    if pass_through:
        p = passThroughSigmoid(x)  # TODO hard sigmoid? pass though it typically used when we don't do slope annealing
    else:
        p = tf.sigmoid(slope_tensor*x) # TODO hard sigmoid
    
    if stochastic:
        return bernoulliSample(p)
    else:
        return binaryRound(p)

def binaryStochastic_REINFORCE(x, stochastic = True, loss_op_name="loss_by_example"):
    """
    Sigmoid followed by a random sample from a bernoulli distribution according
    to the result (binary stochastic neuron). Uses the REINFORCE estimator.
    See https://arxiv.org/abs/1308.3432.
    
    NOTE: Requires a loss operation with name matching the argument for loss_op_name 
    in the graph. This loss operation should be broken out by example (i.e., not a
    single number for the entire batch).
    """
    g = tf.get_default_graph()

    with ops.name_scope("BinaryStochasticREINFORCE"):    
        with g.gradient_override_map({"Sigmoid": "BinaryStochastic_REINFORCE", 
                                      "Ceil": "Identity"}):
            p = tf.sigmoid(x)
            
            reinforce_collection = g.get_collection("REINFORCE")
            if not reinforce_collection:
                g.add_to_collection("REINFORCE", {})
                reinforce_collection = g.get_collection("REINFORCE")
            reinforce_collection[0][p.op.name] = loss_op_name
            
            return tf.ceil(p - tf.random_uniform(tf.shape(x)))

            
@ops.RegisterGradient("BinaryStochastic_REINFORCE")
def _binaryStochastic_REINFORCE(op, _):
    """Unbiased estimator for binary stochastic function based on REINFORCE."""
    loss_op_name = op.graph.get_collection("REINFORCE")[0][op.name]
    loss_tensor = op.graph.get_operation_by_name(loss_op_name).outputs[0]
    
    sub_tensor = op.outputs[0].consumers()[0].outputs[0] #subtraction tensor
    ceil_tensor = sub_tensor.consumers()[0].outputs[0] #ceiling tensor
    
    outcome_diff = (ceil_tensor - op.outputs[0])
    
    # Provides an early out if we want to avoid variance adjustment for
    # whatever reason (e.g., to show that variance adjustment helps)
    if op.graph.get_collection("REINFORCE")[0].get("no_variance_adj"):
        return outcome_diff * tf.expand_dims(loss_tensor, 1)
    
    outcome_diff_sq = tf.square(outcome_diff)
    outcome_diff_sq_r = tf.reduce_mean(outcome_diff_sq, reduction_indices=0)
    outcome_diff_sq_loss_r = tf.reduce_mean(outcome_diff_sq * tf.expand_dims(loss_tensor, 1),
                                            reduction_indices=0)
    
    L_bar_num = tf.Variable(tf.zeros(outcome_diff_sq_r.get_shape()), trainable=False)
    L_bar_den = tf.Variable(tf.ones(outcome_diff_sq_r.get_shape()), trainable=False)
    
    #Note: we already get a decent estimate of the average from the minibatch
    decay = 0.95 
    train_L_bar_num = tf.assign(L_bar_num, L_bar_num*decay +\
                                            outcome_diff_sq_loss_r*(1-decay))
    train_L_bar_den = tf.assign(L_bar_den, L_bar_den*decay +\
                                            outcome_diff_sq_r*(1-decay))


    with tf.control_dependencies([train_L_bar_num, train_L_bar_den]):   
        L_bar = train_L_bar_num/(train_L_bar_den+1e-4)
        L = tf.tile(tf.expand_dims(loss_tensor,1),
                    tf.constant([1,L_bar.get_shape().as_list()[0]]))
        return outcome_diff * (L - L_bar)

def binary_wrapper(pre_activations_tensor,
                   estimator=StochasticGradientEstimator.ST,
                   stochastic_tensor=tf.constant(True), 
                   pass_through=True, 
                   slope_tensor=tf.constant(1.0)):
    """
    Turns a layer of pre-activations (logits) into a layer of binary stochastic neurons
    
    Keyword arguments:
    *estimator: either ST or REINFORCE
    *stochastic_tensor: a boolean tensor indicating whether to sample from a bernoulli 
        distribution (True, default) or use a step_function (e.g., for inference)
    *pass_through: for ST only - boolean as to whether to substitute identity derivative on the 
        backprop (True, default), or whether to use the derivative of the sigmoid
    *slope_tensor: for ST only - tensor specifying the slope for purposes of slope annealing
        trick
    """
    if estimator == StochasticGradientEstimator.ST:
        if pass_through:
            return tf.cond(stochastic_tensor, 
                           lambda: binaryStochastic_ST(pre_activations_tensor), 
                           lambda: binaryStochastic_ST(pre_activations_tensor, stochastic=False))  
        else:
          return tf.cond(stochastic_tensor, 
                         lambda: binaryStochastic_ST(pre_activations_tensor, slope_tensor = slope_tensor, 
                                                     pass_through=False), 
                         lambda: binaryStochastic_ST(pre_activations_tensor, slope_tensor = slope_tensor, 
                                                     pass_through=False, stochastic=False))
    elif estimator == StochasticGradientEstimator.REINFORCE:
        # binaryStochastic_REINFORCE was designed to only be stochastic, so using the ST version
        # for the step fn for purposes of using step fn at evaluation / not for training
        return tf.cond(stochastic_tensor,
                       lambda: binaryStochastic_REINFORCE(pre_activations_tensor),
                       lambda: binaryStochastic_ST(pre_activations_tensor, stochastic=False))
    
    else:
      raise ValueError("Unrecognized estimator.")
# TODO end s2st code


_HmLstmStateTuple = collections.namedtuple("LSTMStateTuple", ("c", "h", "z"))


class HmLstmStateTuple(_HmLstmStateTuple):
  """Tuple used by HmLstm Cells for `state_size`, `zero_state`, and output state.

  Stores three elements: `(c, h, z)`, in that order.
  """
  __slots__ = ()

  @property
  def dtype(self):
    (c, h, z) = self
    if not c.dtype == h.dtype:
      raise TypeError("Inconsistent internal state: %s vs %s" %
                      (str(c.dtype), str(h.dtype)))
    return c.dtype

  
class HmLstmCell(tf.nn.rnn_cell.RNNCell):
  def __init__(self, num_units):
    # self._num_units determines the size of c and h.
    self._num_units = num_units

  @property
  def state_size(self):
    return HmLstmStateTuple(self._num_units, self._num_units, 1)

  @property
  def output_size(self):
    return self._num_units

  # TODO make the type changeable instead of defaulting to float32?
  
  # All RNNs return (output, new_state). For this type of LSTM, its 'output' is still its h vector,
  # and it's cell state is a c,h,z tuple.
  # 'inputs' are a tuple of h_bottom, z_bottom, and h_top_prev.
  # 'state' is a c,h,z HmLstmTuple
  def __call__(self, inputs, state, scope=None):
    # vars from different layers.
    h_bottom, z_bottom, h_top_prev = inputs
    # vars from the previous time step on the same layer
    c_prev, h_prev, z_prev = state

    num_rows = 4 * self._num_units + 1

    # scope: optional name for the variable scope, defaults to "HmLstmCell"
    with vs.variable_scope(scope or type(self).__name__):  # "HmLstmCell"
      # Matrix U_l^l
      U_curr = vs.get_variable("U_curr", [h_prev.get_shape()[1], num_rows], dtype=tf.float32)
      # Matrix U_{l+1}^l
      # TODO This imples that the U matrix there has the same dimensionality as the
      # one used in equation 5. but that would only be true if you forced the h vectors
      # on the above layer to be equal in size to the ones below them. Is that a real restriction?
      # Or am I misunderstanding?
      U_top = vs.get_variable("U_top", [h_bottom.get_shape()[1], num_rows], dtype=tf.float32)
      # Matrix W_{l-1}^l
      W_bottom = vs.get_variable("W_bottom", [h_bottom.get_shape()[1], num_rows],
                                 dtype=tf.float32)
      # b_l
      bias = vs.get_variable("bias", [num_rows], dtype=tf.float32)

      s_curr = tf.matmul(h_prev, U_curr)
      s_top = z_prev * tf.matmul(h_top_prev, U_top)
      s_bottom = z_bottom * tf.matmul(h_bottom, W_bottom)
      gate_logits = s_curr + s_top + s_bottom + bias

      f_logits = tf.slice(gate_logits, [0, 0], [-1, self._num_units])
      i_logits = tf.slice(gate_logits, [0, self._num_units], [-1, self._num_units])
      o_logits = tf.slice(gate_logits, [0, 2*self._num_units], [-1, self._num_units])
      g_logits = tf.slice(gate_logits, [0, 3*self._num_units], [-1, self._num_units])
      z_t_logit = tf.slice(gate_logits, [0, 4*self._num_units], [-1, 1])
      
      f = tf.sigmoid(f_logits)
      i = tf.sigmoid(i_logits)
      o = tf.sigmoid(o_logits)
      g = tf.tanh(g_logits)

      # TODO commenting out all this until we figure out how to do the fancy gradient stuff.
      # Just doing the 'update' move all the time, which is basically normal LSTM.
      c_new = f * c_prev + i * g
      h_new = o * tf.tanh(c_new)
#TODO      z_new = tf.sigmoid(z_t_logit)
      # TODO slope annealing
      z_new = binary_wrapper(z_t_logit,
                             estimator=StochasticGradientEstimator.ST,
                             pass_through=False, # TODO make this true if you do slope annealing
                             stochastic_tensor=tf.constant(True), # TODO make this false if you do slope annealing
                             slope_tensor=None) # TODO set this if you do slope annealing
      
      # slope = 1 # TODO slope annealing trick
      # z_tilda = tf.maximum(0, tf.minimum(1, (slope * z_t_logit) / 2))
      # TODO you have to do something special with the gradient here.
      # z_new = 1 if z_tilda > 0.5 else 0

      # TODO use tf.cond to actually skip computation 
      # if z_prev == 0 and z_below == 1:  # UPDATE
      #   c_new = f * c_prev + i * g
      #   h_new = o * tf.tanh(c_new)
      # elif z_prev == 0 and z_below == 0:  # COPY
      #   c_new = c_prev
      #   h_new = h_prev
      # elif z_prev == 1:  # FLUSH
      #   c_new = i * g
      #   h_new = o * tf.tanh(c_new)
      # else:
      #   raise Exception('Invalid z values. z_prev: {0:.4f}, z_below: {1:.4f}'.format(
      #     z_prev, z_below))

    state_new = HmLstmStateTuple(c_new, h_new, z_new)
    return h_new, state_new



# The output for this is a list of h_vectors, one for each cell.
class MultiHmRNNCell(tf.nn.rnn_cell.RNNCell):
  def __init__(self, cells, output_embedding_size):
    """Create a RNN cell composed sequentially of a number of HmRNNCells.

    Args:
      cells: list of HmRNNCells that will be composed in this order.
    """
    if not cells:
      raise ValueError("Must specify at least one cell for MultiHmRNNCell.")
    self._cells = cells
    self._output_embedding_size = output_embedding_size

  @property
  def state_size(self):
    return tuple(cell.state_size for cell in self._cells)

  @property
  def output_size(self):
    return self._output_embedding_size

  # 'inputs' should be a batch of word vectors
  # 'state' should be a list of HM cell state tuples of the same length as self._cells
  # 'slope' is a scalar tensor for slope annealing.
  def __call__(self, inputs, state, scope=None):
    """Run this multi-layer cell on inputs, starting from state."""
    assert len(state) == len(self._cells)
    with vs.variable_scope(scope or type(self).__name__):  # "MultiHmRNNCell"
      if len(self._cells) > 1:
        h_prev_top = state[1].h
      else:
        h_prev_top = tf.zeros(state[0].h.get_shape())
      # h_bottom, z_bottom, h_prev_top
      current_input = inputs, tf.ones([inputs.get_shape()[0], 1]), h_prev_top
      new_h_list = []
      new_states = []
      # Go through each cell in the different layers, going bottom to top
      for i, cell in enumerate(self._cells):
        with vs.variable_scope("Cell%d" % i):
          new_h, new_state = cell(current_input, state[i]) # state[i] = c_prev, h_prev, z_prev
          assert new_h == new_state.h
          # Set up the inputs for the next cell.
          if i < len(self._cells) - 2:
            # Next cell is not the top one.
            h_prev_top = state[i+2].h
          else:
            # The next cell is the top one, so give it zeros for its h_prev_top input.
            h_prev_top = tf.zeros(state[i].h.get_shape())
          current_input = new_state.h, new_state.z, h_prev_top  # h_bottom, z_bottom, h_prev_top
          new_h_list.append(new_h)
          new_states.append(new_state)
      # Output layer
      with vs.variable_scope("Output"):
        concat_new_h = tf.concat(0, new_h_list)
        output_logits = []
        for i in range(len(new_h_list)):
          # w^l
          gating_unit_weight = vs.get_variable("w{}".format(i), concat_new_h.get_shape(), dtype=tf.float32)
          # g_t^l
          gating_unit = tf.sigmoid(tf.reduce_sum(gating_unit_weight * concat_new_h))
          # W_l^e
          output_embedding_matrix = vs.get_variable("W{}".format(i), 
                                                    [self._output_embedding_size, new_h_list[i].get_shape()[1]], dtype=tf.float32)
          output_logit = gating_unit * tf.matmul(new_h_list[i], output_embedding_matrix)
          output_logits.append(output_logit)
        output_h = tf.nn.relu(tf.add_n(output_logits))
    return output_h, tuple(new_states)




