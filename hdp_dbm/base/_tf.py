import os
import json
import tensorflow as tf

from _base import BaseModel


def is_weight_name(name):
    return not name.startswith('_') and name.endswith('_')


class TensorFlowModel(BaseModel):
    def __init__(self, model_dirpath='tf_model/', save_model=True):
        super(TensorFlowModel, self).__init__()
        self._model_dirpath = None
        self._model_filepath = None
        self._params_filepath = None
        self._summary_dirpath = None
        self._setup_working_dirs(model_dirpath)

        self.save_model = save_model
        self.called_fit = False
        self._tf_merged_summaries = None
        self._tf_saver = None
        self._tf_session = None
        self._tf_summary_writer = None

    def _setup_working_dirs(self, model_dirpath):
        self._model_dirpath = model_dirpath
        self._model_filepath = os.path.join(model_dirpath, 'model')
        self._params_filepath = os.path.join(model_dirpath, 'params.json')
        self._summary_dirpath = os.path.join(model_dirpath, 'logs')

    def _make_tf_model(self):
        raise NotImplementedError

    def _init_tf_ops(self):
        """Initialize all TF variables, operations etc."""
        init_op = tf.global_variables_initializer()
        self._tf_session.run(init_op)
        self._tf_merged_summaries = tf.summary.merge_all()
        self._tf_saver = tf.train.Saver()
        if self.save_model:
            self._tf_summary_writer = tf.summary.FileWriter(self._summary_dirpath,
                                                            self._tf_session.graph)

    def _save_model(self, json_params=None, tf_save_params=None):
        json_params = json_params or {}
        tf_save_params = tf_save_params or {}
        json_params.setdefault('sort_keys', True)
        json_params.setdefault('indent', 4)
        # (recursively) create all folders needed
        if not os.path.exists(self._summary_dirpath):
            os.makedirs(self._summary_dirpath)
        # save params
        params = self.get_params(deep=False)
        with open(self._params_filepath, 'w') as params_file:
            json.dump(params, params_file, **json_params)
        # save tf model
        self._tf_saver.save(self._tf_session, self._model_filepath, **tf_save_params)

    @classmethod
    def load_model(cls, model_dirpath):
        model = cls(model_dirpath=model_dirpath)
        # update paths
        model._setup_working_dirs(model_dirpath)
        # load params
        with open(model._params_filepath, 'r') as params_file:
            params = json.load(params_file)
        model.set_params(**params)
        # load tf model
        model._make_tf_model()
        model._tf_saver = tf.train.Saver()
        with tf.Session() as model._tf_session:
            init_op = tf.global_variables_initializer()
            model._tf_session.run(init_op)
            model._tf_saver.restore(model._tf_session, model._model_filepath)
        return model

    def _fit(self, X, *args, **kwargs):
        """Class-specific `fit` routine."""
        raise NotImplementedError()

    def fit(self, X, *args, **kwargs):
        """Fit the model according to the given training data."""
        if not self.called_fit:
            self._make_tf_model()
        with tf.Session() as self._tf_session:
            self._init_tf_ops()
            self._fit(X, *args, **kwargs)
            self.called_fit = True
            if self.save_model:
                self._save_model()
        return self

    def get_weights(self):
        """Get weights of the model.

        Returns
        -------
        weights : dict
            Weights of the model in form on numpy arrays.
        """
        if not self.called_fit:
            raise ValueError('`fit` must be called before calling `get_weights`')
        if not self.save_model:
            raise RuntimeError('model not found, rerun with `save_model`=True')
        # collect and filter all attributes
        weights = vars(self)
        weights = {key: weights[key] for key in weights if is_weight_name(key)}
        # evaluate the respective variables
        with tf.Session() as self._tf_session:
            self._tf_saver.restore(self._tf_session, self._model_filepath)
            for key, value in weights.items():
                weights[key] = value.eval()
        return weights