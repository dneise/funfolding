import numpy as np
import pymc3 as pm

from ..model import LinearModel, Model
import theano


def create_C_thikonov(n_dims, crop_beginning=False, crop_end=False):
    C = np.zeros((n_dims, n_dims))
    if not crop_beginning:
        C[0, 0] = -1
        C[0, 1] = 1
    idx_N = n_dims - 1
    for i in range(1, idx_N):
        C[i, i] = -2.
        C[i, i - 1] = 1
        C[i, i + 1] = 1
    if not crop_end:
        C[idx_N, idx_N] = -1
        C[idx_N, idx_N - 1] = 1
    return C


class LLH(object):
    name = 'LLH'
    status_need_for_eval = 0

    def __init__(self):
        self.status = -1

        self.vec_g = None
        self.model = None

        self.gradient_defined = False
        self.hessian_matrix_defined = False

    def initialize(self):
        self.status = 0

    def evaluate_llh(self):
        if self.status < 0:
            raise RuntimeError("LLH has to be intilized. "
                               "Run 'LLH.initialize' first!")

    def evaluate_gradient(self):
        if self.gradient_defined:
            if self.status < 0:
                raise RuntimeError("LLH has to be intilized. "
                                   "Run 'LLH.initialize' first!")
        else:
            raise NotImplementedError("Gradients are not implemented!")

    def evaluate_hessian(self):
        if self.hessian_matrix_defined:
            if self.status < 0:
                raise RuntimeError("LLH has to be intilized. "
                                   "Run 'LLH.initialize' first!")
        else:
            raise NotImplementedError("hessian Matrix is not implemented!")

    def __call__(self, f):
        return self.evaluate_llh(f)


class StandardLLHAlt(LLH):
    name = 'StandardLLH'
    status_need_for_eval = 0

    def __init__(self,
                 tau=None,
                 C='thikonov',
                 vec_acceptance=None,
                 log_f=False):
        super(StandardLLH, self).__init__()
        self.C = C
        self.tau = tau
        self.log_f_reg = log_f
        self.vec_acceptance = vec_acceptance

    def __call__(self, f):
        return self.evaluate_llh(f)

    def initialize(self,
                   vec_g,
                   model,
                   crop_C_beginning=False,
                   crop_C_end=False):
        super(StandardLLH, self).initialize()
        if not isinstance(model, Model):
            raise ValueError("'model' has to be of type Model!")
        self.model = model
        self.vec_g = vec_g
        self.N = np.sum(vec_g)

        if self.vec_acceptance is not None:
            if len(self.vec_acceptance) != model.dim_f:
                raise ValueError("'vec_acceptance' has to be of the same "
                                 "length as vec_f!")
            self.vec_acceptance = self.vec_acceptance
        else:
            self.vec_acceptance = np.ones(model.dim_f)

        if self.tau is None:
            self._tau = None
        else:
            if isinstance(self.tau, float):
                if self.tau <= 0.:
                    self._tau = None
                else:
                    self._tau = np.ones(model.dim_f) * self.tau
            elif callable(self.tau):
                self._tau = self.tau(np.arange(model.dim_f))
            else:
                raise ValueError("'tau' as to be either None, float or "
                                 "callable!")
            if self._tau is not None:
                if isinstance(self.C, str):
                    if self.C.lower() == 'thikonov' or self.C.lower() == '2':
                        m_C = create_C_thikonov(
                            model.dim_f,
                            crop_end=crop_C_end,
                            crop_beginning=crop_C_beginning)
                elif isinstance(self.C, int):
                    if self.C == 2:
                        m_C = create_C_thikonov(model.dim_f)
                if m_C is None:
                    raise ValueError("{} invalid option for 'C'".format(
                        self.C))
                self._C = np.dot(np.dot(m_C, np.diag(1 / self._tau)), m_C)

        if isinstance(model, LinearModel):
            self.gradient_defined = True
            self.hessian_matrix_defined = True

    def evaluate_llh(self, f):
        super(StandardLLH, self).evaluate_llh()
        g_est, f, f_reg = self.model.evaluate(f)
        if any(g_est < 0) or any(f < 0):
            return np.inf * -1
        poisson_part = np.sum(self.vec_g * np.log(g_est) - g_est)
        if self._tau is not None:
            f_reg_used = f_reg * self.vec_acceptance
            if self.log_f_reg:
                f_reg_used = np.log10(f_reg_used + 1)
            reg_part = 0.5 * np.dot(
                np.dot(f_reg_used.T, self._C), f_reg_used)
        else:
            reg_part = 0
        return poisson_part - reg_part

    def evaluate_neg_llh(self, f):
        return self.evaluate_llh(f) * -1.

    def evaluate_gradient(self, f):
        super(StandardLLH, self).evaluate_gradient()
        g_est, f, f_reg = self.model.evaluate(f)
        part_b = np.sum(self.model.A, axis=0)
        h_unreg = np.sum(self.model.A.T * self.vec_g * (1 / g_est), axis=1)
        h_unreg -= part_b
        if self._tau is not None:
            if self.log_f_reg:
                reg_part = np.zeros(self.model.dim_f)
                f_used = f_reg * self.vec_acceptance + 1
                ln_f_used = np.log(f_used)
                ln_10_squared = np.log(10)**2
                pre = np.zeros((self.model.dim_f,
                                self.model.dim_f))
                for i in range(self.model.dim_f):
                    for j in range(self.model.dim_f):
                        pre_part_ij = self.vec_acceptance[i] * self._C[i, j]
                        pre_part_ij *= ln_f_used[j]
                        pre_part_ij /= ln_10_squared * f_used[i]
                        pre[i] += pre_part_ij
                for i in range(self.model.dim_f):
                    reg_part_i = np.sum(pre[i, :])
                    reg_part_i += np.sum(pre[:, i])

            else:
                reg_part = np.dot(self._C, f_reg * self.vec_acceptance)
        else:
            reg_part = 0.
        return h_unreg - reg_part

    def evaluate_neg_gradient(self, f):
        return self.evaluate_gradient(f) * -1.

    def evaluate_hessian(self, f):
        super(StandardLLH, self).evaluate_hessian()
        g_est, f, f_reg = self.model.evaluate(f)
        H_unreg = -np.dot(np.dot(self.model.A.T,
                                 np.diag(self.vec_g / g_est**2)),
                          self.model.A)
        if self._tau is not None:
            if self.log_f_reg:
                reg_part = np.zeros((self.model.dim_f,
                                     self.model.dim_f))
                f_used = f_reg * self.vec_acceptance + 1
                ln_f_used = np.log(f_used)
                ln_10_squared = np.log(10)**2
                for i in range(self.model.dim_f):
                    for j in range(i + 1):
                        r = (self._C[i, j] + self._C[j, i])
                        r *= self.vec_acceptance[i] * self.vec_acceptance[j]
                        r /= ln_10_squared * f_used[i] * f_used[j]
                        if i == j:
                            r_diag = -self.vec_acceptance[j]**2 / ln_10_squared
                            r_diag = f_used**2
                            r_diag = np.sum((self._C[i, :] + self._C[:, i]) *
                                            ln_f_used)
                            reg_part[i, i] = r + r_diag
                        else:
                            reg_part[i, j] = r
                            reg_part[j, i] = r
            else:
                reg_part = self._C
        else:
            reg_part = 0.

        return H_unreg - reg_part

    def evaluate_neg_hessian(self, f):
        return self.evaluate_hessian(f) * -1.


class StandardLLH(LLH):
    name = 'StandardLLH'
    status_need_for_eval = 0

    def __init__(self,
                 tau=None,
                 C='thikonov',
                 vec_acceptance=None,
                 log_f=False):
        super(StandardLLH, self).__init__()
        self.C = C
        self.tau = tau
        self.log_f_reg = log_f
        self.vec_acceptance = vec_acceptance

    def __call__(self, f):
        return self.evaluate_llh(f)

    def initialize(self,
                   vec_g,
                   model,
                   crop_C_beginning=False,
                   crop_C_end=False):
        super(StandardLLH, self).initialize()
        if not isinstance(model, Model):
            raise ValueError("'model' has to be of type Model!")
        self.model = model
        self.vec_g = vec_g
        self.N = np.sum(vec_g)

        if self.vec_acceptance is not None:
            if len(self.vec_acceptance) != model.dim_f:
                raise ValueError("'vec_acceptance' has to be of the same "
                                 "length as vec_f!")
            self.vec_acceptance = self.vec_acceptance
        else:
            self.vec_acceptance = np.ones(model.dim_f)

        if self.tau is None:
            self._tau = None
        else:
            if isinstance(self.tau, float):
                if self.tau <= 0.:
                    self._tau = None
                else:
                    self._tau = np.ones(model.dim_f) * self.tau
            elif callable(self.tau):
                self._tau = self.tau(np.arange(model.dim_f))
            else:
                raise ValueError("'tau' as to be either None, float or "
                                 "callable!")
            if self._tau is not None:
                if isinstance(self.C, str):
                    if self.C.lower() == 'thikonov' or self.C.lower() == '2':
                        m_C = create_C_thikonov(
                            model.dim_f,
                            crop_end=crop_C_end,
                            crop_beginning=crop_C_beginning)
                elif isinstance(self.C, int):
                    if self.C == 2:
                        m_C = create_C_thikonov(model.dim_f)
                if m_C is None:
                    raise ValueError("{} invalid option for 'C'".format(
                        self.C))
                self._C = np.dot(np.dot(m_C, np.diag(1 / self._tau)), m_C)

        if isinstance(model, LinearModel):
            self.gradient_defined = True
            self.hessian_matrix_defined = True

    def evaluate_llh(self, f):
        super(StandardLLH, self).evaluate_llh()
        g_est, f, f_reg = self.model.evaluate(f)
        if any(g_est < 0) or any(f < 0):
            return np.inf * -1
        poisson_part = np.sum(self.vec_g * np.log(g_est) - g_est)
        if self._tau is not None:
            if self.log_f_reg:
                f_reg_used = np.log10((f_reg + 1) * self.vec_acceptance)
            else:
                f_reg_used = f_reg * self.vec_acceptance
            reg_part = 0.5 * np.dot(
                np.dot(f_reg_used.T, self._C), f_reg_used)
        else:
            reg_part = 0
        return poisson_part - reg_part

    def create_pymc_model(self, x0=None):
        super(StandardLLH, self).evaluate_llh()
        model = pm.Model()

        if x0 is None:
            x0 = np.sum(self.vec_g) / self.model.dim_f
            x0 = np.ones(self.model.dim_f, dtype=float) * x0

        with model:
            A = theano.shared(self.model.A)
            vec_g = theano.shared(self.vec_g)
            f = pm.Uniform('f',
                           testval=x0,
                           lower=0,
                           upper=np.sum(self.vec_g),
                           shape=self.model.dim_f)
            g_est = theano.tensor.dot(A, f)
            poisson_part = pm.Poisson('poisson_part_llh',
                                      mu=g_est,
                                      observed=vec_g)
            if self.tau > 0:
                vec_acceptance = theano.shared(self.vec_acceptance)
                _C = theano.shared(self._C)
                if self.log_f_reg:
                    def calc_reg_part(f_reg):
                        f_reg_used = theano.tensor.log10(
                            (f_reg + 1) * vec_acceptance)
                        return 0.5 * theano.tensor.dot(
                            theano.tensor.dot(f_reg_used.T, _C),
                            f_reg_used)
                else:
                    def calc_reg_part(f_reg):
                        return 0.5 * theano.tensor.dot(
                            theano.tensor.dot(f_reg.T, _C),
                            f_reg)
                reg_part = pm.Deterministic('reg_part', calc_reg_part(f))
            else:
                reg_part = 0.
            pm.Deterministic('logp',
                             theano.tensor.sum(poisson_part) - reg_part)
        return model

    def evaluate_neg_llh(self, f):
        return self.evaluate_llh(f) * -1.

    def evaluate_gradient(self, f):
        super(StandardLLH, self).evaluate_gradient()
        g_est, f, f_reg = self.model.evaluate(f)
        part_b = np.sum(self.model.A, axis=0)
        h_unreg = np.sum(self.model.A.T * self.vec_g * (1 / g_est), axis=1)
        h_unreg -= part_b
        if self._tau is not None:
            if self.log_f_reg:
                reg_part = np.zeros(self.model.dim_f)
                denom_f = f_reg + 1
                nom_f = np.log(denom_f * self.vec_acceptance)
                ln_10_squared = np.log(10)**2
                pre = np.zeros((self.model.dim_f,
                                self.model.dim_f))
                for i in range(self.model.dim_f):
                    for j in range(self.model.dim_f):
                        pre[i, j] = self._C[i, j] * nom_f[i]
                        pre[i, j] /= ln_10_squared * denom_f[i]
                for i in range(self.model.dim_f):
                    reg_part_i = np.sum(pre[i, :])
                    reg_part_i += np.sum(pre[:, i])
            else:
                reg_part = np.dot(self._C, f_reg * self.vec_acceptance)
        else:
            reg_part = 0.
        return h_unreg - reg_part

    def evaluate_neg_gradient(self, f):
        return self.evaluate_gradient(f) * -1.

    def evaluate_hessian(self, f):
        super(StandardLLH, self).evaluate_hessian()
        g_est, f, f_reg = self.model.evaluate(f)
        H_unreg = -np.dot(np.dot(self.model.A.T,
                                 np.diag(self.vec_g / g_est**2)),
                          self.model.A)
        if self._tau is not None:
            if self.log_f_reg:
                pre = np.dot(np.dot(np.diag(f_reg + 1), self._C),
                             np.diag(f_reg + 1)) / np.log(10)**2
                ln_f_used = np.log((f_reg + 1) * self.vec_acceptance)
                pre_diag_1 = np.dot(pre, np.diag(ln_f_used))
                pre_diag_2 = np.dot(np.diag(ln_f_used), pre)
                reg_part = np.zeros_like(pre)
                for i in range(self.model.dim_f):
                    for j in range(i + 1):
                        r = pre[i, j] + pre[j, i]
                        if i == j:
                            r += np.sum(pre_diag_1, axis=1)
                            r += np.sum(pre_diag_2, axis=1)
                            reg_part[i, j]
                        else:
                            reg_part[i, j] = r
                            reg_part[j, i] = r
            else:
                reg_part = self._C
        else:
            reg_part = 0.

        return H_unreg - reg_part

    def evaluate_neg_hessian(self, f):
        return self.evaluate_hessian(f) * -1.


class LLHThikonovForLoops:
    def __init__(self, g, linear_model, tau):
        if not isinstance(linear_model, LinearModel):
            raise ValueError("'model' has to be of type LinearModel!")
        self.linear_model = linear_model
        self.n_dims_f = linear_model.A.shape[1]
        self.g = g
        self.C = create_C_thikonov(self.n_dims_f)
        self.tau = tau
        self.status = 0

    def evaluate_llh(self, f):
        m, n = self.linear_model.A.shape
        poisson_part = 0
        for i in range(m):
            g_est = 0
            for j in range(n):
                g_est += self.linear_model.A[i, j] * f[j]
            poisson_part += g_est - self.g[i] * np.log(g_est)

        reg_part = 0
        for i in range(n):
            for j in range(n):
                reg_part += self.C[i, j] * f[i] * f[j]
        reg_part *= 0.5 * self.tau
        return reg_part - poisson_part

    def evaluate_gradient(self, f):
        m, n = self.linear_model.A.shape
        gradient = np.zeros(n)
        for k in range(n):
            poisson_part = 0
            for i in range(m):
                g_est = 0
                for j in range(n):
                    g_est += self.linear_model.A[i, j] * f[j]
                A_ik = self.linear_model.A[i, k]
                poisson_part += A_ik - (self.g[i] * A_ik) / g_est
            c = 0
            for i in range(n):
                c += self.C[i, k] * f[i]
            reg_part = self.tau * c
            gradient[k] = reg_part - poisson_part
        return gradient

    def evaluate_hessian(self, f):
        m, n = self.linear_model.A.shape
        hess = np.zeros((n, n))
        for k in range(n):
            for l in range(n):
                poisson_part = 0
                for i in range(m):
                    A_ik = self.linear_model.A[i, k]
                    A_il = self.linear_model.A[i, l]
                    nominator = self.g[i] * A_ik * A_il
                    denominator = 0
                    for j in range(n):
                        denominator += self.linear_model.A[i, j] * f[j]
                    poisson_part += nominator / denominator**2
                hess[k, l] = poisson_part + self.tau * self.C[k, l]
        return hess
