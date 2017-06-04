import numpy as np
import tensorflow as tf
from matplotlib import pyplot as plt
from tensorflow.core.framework import summary_pb2

from base import TensorFlowModel, run_in_tf_session
from utils import batch_iter, tbatch_iter
from utils.dataset import load_mnist


class BaseRBM(TensorFlowModel):
    """
    References
    ----------
    [1] Goodfellow I. et. al. Deep Learning.
    [2] Hinton, G. A Practical Guide to Training
        Restricted Boltzmann Machines (ver.1, 2010).
    [3] Restricted Boltzmann Machines (RBMs).
        http://deeplearning.net/tutorial/rbm.html
    """
    def __init__(self, n_visible=784, n_hidden=256, w_std=0.01, n_gibbs_steps=1,
                 learning_rate=0.1, momentum=0.9,
                 batch_size=10, max_epoch=10, compute_metrics_every=10,
                 verbose=False, model_path='rbm_model/', **kwargs):
        super(BaseRBM, self).__init__(model_path=model_path, **kwargs)
        self.n_visible = n_visible
        self.n_hidden = n_hidden
        self.w_std = w_std
        self.n_gibbs_steps = n_gibbs_steps
        self.learning_rate = learning_rate
        self.momentum = momentum
        self.batch_size = batch_size
        self.max_epoch = max_epoch
        self.compute_metrics_every = compute_metrics_every
        self.verbose = verbose

        # current epoch and iteration
        self.epoch = 0
        self.iter = 0

        # input data
        self._X_batch = None
        self._h_rand = None
        self._v_rand = None

        # weights
        self._W = None
        self._hb = None
        self._vb = None

        # grads
        self._dW = None
        self._dhb = None
        self._dvb = None

        # operations
        self._train_op = None
        self._transform_op = None
        self._msre = None

    def _make_init_op(self):
        # create placeholders (input data)
        with tf.name_scope('input_data'):
            self._X_batch = tf.placeholder('float', [None, self.n_visible], name='X_batch')
            self._h_rand = tf.placeholder('float', [None, self.n_hidden], name='h_rand')
            self._v_rand = tf.placeholder('float', [None, self.n_visible], name='v_rand')
            self._learning_rate = tf.placeholder('float', [], name='learning_rate')
            self._momentum = tf.placeholder('float', [], name='momentum')

        # create variables (weights and grads)
        with tf.name_scope('weights'):
            W_tensor = tf.random_normal((self.n_visible, self.n_hidden),
                                        mean=0.0, stddev=self.w_std, seed=self.random_seed)
            self._W = tf.Variable(W_tensor, name='W', dtype=tf.float32)
            self._hb = tf.Variable(tf.zeros((self.n_hidden,)), name='hb', dtype=tf.float32)
            self._vb = tf.Variable(tf.zeros((self.n_visible,)), name='vb', dtype=tf.float32)

        with tf.name_scope('grads'):
            self._dW = tf.Variable(tf.zeros((self.n_visible, self.n_hidden)), name='dW', dtype=tf.float32)
            self._dhb = tf.Variable(tf.zeros((self.n_hidden,)), name='dhb', dtype=tf.float32)
            self._dvb = tf.Variable(tf.zeros((self.n_visible,)), name='dvb', dtype=tf.float32)

    def _propup(self, v):
        with tf.name_scope('prop_up'):
            h = tf.matmul(v, self._W) + self._hb
        return h

    def _propdown(self, h):
        with tf.name_scope('prop_down'):
            v = tf.matmul(a=h, b=self._W, transpose_b=True) + self._vb
        return v

    def _sample_h_given_v(self, v):
        """Sample from P(h|v)."""
        with tf.name_scope('sample_h_given_v'):
            with tf.name_scope('h_means'):
                h_means = tf.nn.sigmoid(self._propup(v))
            with tf.name_scope('h_samples'):
                h_samples = tf.to_float(tf.less(self._h_rand, h_means))
        return h_means, h_samples

    def _sample_v_given_h(self, h):
        """Sample from P(v|h)."""
        with tf.name_scope('sample_v_given_h'):
            with tf.name_scope('v_means'):
                v_means = tf.nn.sigmoid(self._propdown(h))
            with tf.name_scope('v_samples'):
                v_samples = tf.to_float(tf.less(self._v_rand, v_means))
        return v_means, v_samples

    def _make_train_op(self):
        # run Gibbs chain
        with tf.name_scope('gibbs_chain'):
            h0_means, h0_samples = self._sample_h_given_v(self._X_batch)
            h_means, v_means, v_samples = None, None, None
            h_samples = h0_samples
            for _ in xrange(self.n_gibbs_steps):
                with tf.name_scope('sweep'):
                    v_means, v_samples = self._sample_v_given_h(h_samples)
                    h_means, h_samples = self._sample_h_given_v(v_samples)

        # encoded data, used by the transform method
        with tf.name_scope('transform_op'):
            transform_op = tf.identity(h_means)
            tf.add_to_collection('transform_op', transform_op)

        # compute gradients estimates (= positive - negative associations)
        with tf.name_scope('grads_estimates'):
            N = tf.constant(self.batch_size, dtype='float')
            with tf.name_scope('dW'):
                dW_positive = tf.matmul(tf.transpose(self._X_batch), h0_means)
                dW_negative = tf.matmul(tf.transpose(v_samples), h_means)
                dW = (dW_positive - dW_negative) / N
            with tf.name_scope('dhb'):
                dhb = tf.reduce_mean(h0_means - h_means, axis=0) / N
            with tf.name_scope('dvb'):
                dvb = tf.reduce_mean(self._X_batch - v_samples, axis=0) / N

        # update parameters
        with tf.name_scope('momentum_updates'):
            with tf.name_scope('dW'):
                self._dW  = self._momentum * self._dW + dW
                W_update = self._W.assign_add(self._learning_rate * self._dW)
            with tf.name_scope('dhb'):
                self._dhb = self._momentum * self._dhb + dhb
                hb_update = self._hb.assign_add(self._learning_rate * self._dhb)
            with tf.name_scope('dvb'):
                self._dvb = self._momentum * self._dvb + dvb
                vb_update = self._vb.assign_add(self._learning_rate * self._dvb)

        # assemble train_op
        with tf.name_scope('train_op'):
            train_op = tf.group(W_update, hb_update, vb_update)
            tf.add_to_collection('train_op', train_op)

        # compute metrics
        with tf.name_scope('mean_squared_recon_error'):
            msre = tf.reduce_mean(tf.square(self._X_batch - v_means))
            tf.add_to_collection('msre', msre)

        # collect summaries
        tf.summary.scalar('msre', msre)

    def _free_energy(self, v):
        """Compute free energy of a visible vectors `v`."""
        with tf.name_scope('free_energy'):
            fe = -tf.einsum('ij,j->i', v, self._vb)
            fe -= tf.reduce_sum(tf.nn.softplus(self._propup(v)), axis=1)
            fe = tf.reduce_mean(fe, axis=0)
        return fe

    def _pseudo_loglik(self, v):
        """Compute average Pseudo-loglikelihood approximation of `v` as in [3]."""
        pass

    def _make_tf_model(self):
        self._make_init_op()
        self._make_train_op()

    def _make_tf_feed_dict(self, X_batch, is_training=False):
        feed_dict = {}
        feed_dict['input_data/X_batch:0'] = X_batch
        feed_dict['input_data/h_rand:0'] = self._rng.rand(X_batch.shape[0], self.n_hidden)
        feed_dict['input_data/v_rand:0'] = self._rng.rand(X_batch.shape[0], self.n_visible)
        if is_training:
            feed_dict['input_data/learning_rate:0'] = self.learning_rate
            feed_dict['input_data/momentum:0'] = self.momentum
        return feed_dict

    def _train_epoch(self, X):
        train_msres = []
        for X_batch in (tbatch_iter if self.verbose else batch_iter)(X, self.batch_size):
            self.iter += 1
            if self.iter % self.compute_metrics_every == 0:
                _, train_s, train_msre, fe = \
                    self._tf_session.run([self._train_op, self._tf_merged_summaries, self._msre, self._free_energy(tf.constant(X_batch, dtype='float'))],
                                         feed_dict=self._make_tf_feed_dict(X_batch, is_training=True))
                print fe
                self._tf_train_writer.add_summary(train_s, self.iter)
                train_msres.append(train_msre)
            else:
                self._tf_session.run(self._train_op,
                                     feed_dict=self._make_tf_feed_dict(X_batch, is_training=True))
        return np.mean(train_msres)

    def _run_val_metrics(self, X_val):
        val_msres = []
        for X_vb in batch_iter(X_val, batch_size=self.batch_size):
            val_msre = self._tf_session.run(self._msre,
                                            feed_dict=self._make_tf_feed_dict(X_vb))
            val_msres.append(val_msre)
        mean_msre = np.mean(val_msres)
        val_s = summary_pb2.Summary(value=[summary_pb2.Summary.Value(tag="msre",
                                                                     simple_value=mean_msre)])
        self._tf_val_writer.add_summary(val_s, self.iter)
        return mean_msre

    def _fit(self, X, X_val=None):
        self._train_op = tf.get_collection('train_op')[0]
        self._msre = tf.get_collection('msre')[0]
        val_msre = None
        while self.epoch < self.max_epoch:
            self.epoch += 1
            train_msre = self._train_epoch(X)
            if X_val is not None:
                val_msre = self._run_val_metrics(X_val)

            if self.verbose:
                s = "epoch: {0:{1}}/{2}"\
                    .format(self.epoch, len(str(self.max_epoch)), self.max_epoch)
                s += " - train.msre: {0:.4f}".format(train_msre)
                if val_msre: s += " - val.msre: {0:.4f}".format(val_msre)
                print s
            self._save_model(global_step=self.epoch)

    @run_in_tf_session
    def transform(self, X):
        self._transform_op = tf.get_collection('transform_op')[0]
        H = np.zeros((len(X), self.n_hidden))
        start = 0
        for X_b in batch_iter(X, batch_size=self.batch_size):
            H_b = self._transform_op.eval(feed_dict=self._make_tf_feed_dict(X_b))
            H[start:(start + self.batch_size)] = H_b
            start += self.batch_size
        return H


class BernoulliRBM(BaseRBM):
    """Bernoulli-Bernoulli RBM."""
    pass


class GaussianRBM(BaseRBM):
    """Gaussian-Bernoulli RBM."""
    pass


class MultinomialRBM(BaseRBM):
    """Bernoulli-Multinomial RBM."""
    pass


def plot_rbm_filters(W):
    plt.figure(figsize=(12, 12))
    for i in xrange(100):
        filters = W[:, i].reshape((28, 28))
        plt.subplot(10, 10, i + 1)
        plt.imshow(filters, cmap=plt.cm.gray_r, interpolation='nearest')
        plt.xticks(())
        plt.yticks(())
    plt.suptitle('First 100 components extracted by RBM', fontsize=24)


# if __name__ == '__main__':
#     # run corresponding tests
#     from utils.testing import run_tests
#     from tests import test_rbm
#     run_tests(__file__, test_rbm)

if __name__ == '__main__':
    X, _ = load_mnist(mode='train', path='../data/')
    X_val, _ = load_mnist(mode='test', path='../data/')
    X = X[:2000]
    X_val = X_val[:200]
    X /= 255.
    X_val /= 255.

    rbm = BaseRBM(n_visible=784,
                  n_hidden=256,
                  n_gibbs_steps=1,
                  learning_rate=0.01,
                  momentum=0.9,
                  batch_size=10,
                  max_epoch=3,
                  verbose=True,
                  random_seed=1337,
                  model_path='../models/rbm1/')
    rbm.fit(X, X_val)

    # rbm = BaseRBM.load_model('../models/rbm1/')
    # H = rbm.transform(X_val)
    # print H[0][:10]
    # plot_rbm_filters(rbm.get_weights()['W:0'])
    # plt.show()
    # rbm.fit(X)