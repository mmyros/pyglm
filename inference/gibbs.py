""" Fit a Network GLM with MAP estimation. For some models, the log posterior
    is concave and has a unique maximum.
"""

import copy

from utils.theano_func_wrapper import seval, _flatten
from utils.packvec import *
from utils.grads import *

from hmc import hmc
from coord_descent import coord_descent
from log_sum_exp import log_sum_exp_sample


class MetropolisHastingsUpdate(object):
    """
    Base class for MH updates. Each update targets a specific model component
    and requires certain configuration. For example, an update for the standard GLM
    might require differentiable parameters. Typical updates include:
        - Gibbs updates (sample from conditional distribution)
        - Hamiltonian Monte Carlo (uses gradient info to sample unconstrained cont. vars)
        - Slice sampling (good for correlaed multivariate Gaussians)
    """
    def __init__(self):
        self._target_components = []

    @property
    def target_components(self):
        # Return a list of components that this update applies to
        return self._target_components

    @property
    def target_variables(self):
        # Return a list of variables that this update applies to
        return []


    def preprocess(self, population):
        """ Do any req'd preprocessing
        """
        pass

    def update(self, x_curr):
        """ Take a MH step
        """

class ParallelMetropolisHastingsUpdate(MetropolisHastingsUpdate):
    """ Extending this class indicates that the updates can be
        performed in parallel over n, the index of the neuron.
    """
    def update(self, x_curr, n):
        """ Take a MH step for the n-th neuron. This can be performed in parallel 
            over other n' \in [N]
        """
        pass

class HmcGlmUpdate(ParallelMetropolisHastingsUpdate):
    """
    Update the continuous and unconstrained GLM parameters using Hamiltonian
    Monte Carlo. Stochastically follow the gradient of the parameters using
    Hamiltonian dynamics.
    """
    def __init__(self):
        super(HmcGlmUpdate, self).__init__()

        self.avg_accept_rate = 0.9
        self.step_sz = 0.05

    def preprocess(self, population):
        """ Initialize functions that compute the gradient and Hessian of
            the log probability with respect to the differentiable GLM
            parameters, e.g. the weight matrix if it exists.
        """
        self.population = population
        self.glm = population.glm
        self.syms = population.get_variables()
        self.glm_syms = differentiable(self.syms['glm'])

        # Compute gradients of the log prob wrt the GLM parameters
        self.glm_logp = self.glm.log_p
        self.g_glm_logp_wrt_glm, _ = grad_wrt_list(self.glm_logp,
                                                   _flatten(self.glm_syms))

        # Get the shape of the parameters from a sample of variables
        self.glm_shapes = get_shapes(self.population.extract_vars(self.population.sample(),0)['glm'],
                                     self.glm_syms)

    def _glm_logp(self, x_vec, x_all):
        """
        Compute the log probability (or gradients and Hessians thereof)
        of the given GLM variables. We also need the rest of the population variables,
        i.e. those that are not being sampled currently, in order to evaluate the log
        probability.
        """
        # Extract the glm parameters
        x_glm = unpackdict(x_vec, self.glm_shapes)
        set_vars(self.glm_syms, x_all['glm'], x_glm)
        lp = seval(self.glm_logp,
                   self.syms,
                   x_all)
        return lp

    def _grad_glm_logp(self, x_vec, x_all):
        """
        Compute the negative log probability (or gradients and Hessians thereof)
        of the given GLM variables. We also need the rest of the population variables,
        i.e. those that are not being sampled currently, in order to evaluate the log
        probability.
        """
        # Extract the glm parameters
        x_glm = unpackdict(x_vec, self.glm_shapes)
        set_vars(self.glm_syms, x_all['glm'], x_glm)
        glp = seval(self.g_glm_logp_wrt_glm,
                    self.syms,
                    x_all)
        return glp

    def update(self, x, n):
        """ Gibbs sample the GLM parameters. These are mostly differentiable
            so we use HMC wherever possible.
        """

        xn = self.population.extract_vars(x, n)

        # Get the differentiable variables suitable for HMC
        dxn = get_vars(self.glm_syms, xn['glm'])
        x_glm_0, shapes = packdict(dxn)

        # Create lambda functions to compute the nll and its gradient
        nll = lambda x_glm_vec: -1.0*self._glm_logp(x_glm_vec, xn)
        grad_nll = lambda x_glm_vec: -1.0*self._grad_glm_logp(x_glm_vec, xn)

        # HMC with automatic parameter tuning
        n_steps = 2
        x_glm, new_step_sz, new_accept_rate = hmc(nll,
                                                  grad_nll,
                                                  self.step_sz,
                                                  n_steps,
                                                  x_glm_0,
                                                  adaptive_step_sz=True,
                                                  avg_accept_rate=self.avg_accept_rate)

        # Update step size and accept rate
        self.step_sz = new_step_sz
        self.avg_accept_rate = new_accept_rate
        # print "GLM step sz: %.3f\tGLM_accept rate: %.3f" % (new_step_sz, new_accept_rate)


        # Unpack the optimized parameters back into the state dict
        x_glm_n = unpackdict(x_glm, shapes)
        set_vars(self.glm_syms, xn['glm'], x_glm_n)


        x['glms'][n] = xn['glm']
        return x


class CollapsedGibbsNetworkColumnUpdate(ParallelMetropolisHastingsUpdate):

    def __init__(self):
        super(CollapsedGibbsNetworkColumnUpdate, self).__init__()

        # TODO: Only use an MH proposal from the prior if you are certain
        # that the prior puts mass on likely edges. Otherwise you will never
        # propose to transition from no-edge to edge and mixing will be very,
        # very slow.
        self.propose_from_prior = False

        # Define constants for Sampling
        self.DEG_GAUSS_HERMITE = 20
        self.GAUSS_HERMITE_ABSCISSAE, self.GAUSS_HERMITE_WEIGHTS = \
            np.polynomial.hermite.hermgauss(self.DEG_GAUSS_HERMITE)

    def preprocess(self, population):
        """ Initialize functions that compute the gradient and Hessian of
            the log probability with respect to the differentiable network
            parameters, e.g. the weight matrix if it exists.
        """
        self.population = population
        self.network = population.network
        self.glm = population.glm
        self.syms = population.get_variables()

        # Get the weight model
        self.mu_w = self.network.weights.prior.mu.get_value()
        self.sigma_w = self.network.weights.prior.sigma.get_value()

        if hasattr(self.network.weights, 'refractory_prior'):
            self.mu_w_ref = self.network.weights.refractory_prior.mu.get_value()
            self.sigma_w_ref = self.network.weights.refractory_prior.sigma.get_value()
        else:
            self.mu_w_ref = self.mu_w
            self.sigma_w_ref = self.sigma_w

    def _precompute_vars(self, x, n_post):
        """ Precompute currents for sampling A and W
        """
        nvars = self.population.extract_vars(x, n_post)

        I_bias = seval(self.glm.bias_model.I_bias,
                       self.syms,
                       nvars)

        I_stim = seval(self.glm.bkgd_model.I_stim,
                       self.syms,
                       nvars)

        I_imp = seval(self.glm.imp_model.I_imp,
                      self.syms,
                      nvars)

        p_A = seval(self.network.graph.pA,
                    self.syms['net'],
                    x['net'])

        return I_bias, I_stim, I_imp, p_A

    def _lp_A(self, n_pre, n_post, v, x):
        """ Compute the log probability for a given entry A[n_pre,n_post]
        """

        # Update A[n_pre, n_post]
        A = x['net']['graph']['A']
        A[n_pre, n_post] = v

        # Get the prior probability of A
        lp = seval(self.network.log_p,
                   self.syms['net'],
                   x['net'])
        return lp

    def _glm_ll_A(self, n_pre, n_post, w, x, I_bias, I_stim, I_imp):
        """ Compute the log likelihood of the GLM with A=True and given W
        """
        # Set A in state dict x
        A = x['net']['graph']['A']
        A_init = A[n_pre, n_post]
        A[n_pre, n_post] = 1

        # Set W in state dict x
        W = x['net']['weights']['W'].reshape(A.shape)
        W_init = W[n_pre, n_post]
        W[n_pre, n_post] = w

        # Get the likelihood of the GLM under A and W
        s = [self.network.graph.A] + \
             _flatten(self.syms['net']['weights']) + \
            [self.glm.n,
             self.glm.bias_model.I_bias,
             self.glm.bkgd_model.I_stim,
             self.glm.imp_model.I_imp] + \
            _flatten(self.syms['glm']['nlin'])

        xv = [A] + \
             [W.ravel()] + \
             [n_post,
              I_bias,
              I_stim,
              I_imp] + \
            _flatten(x['glms'][n_post]['nlin'])

        ll = self.glm.ll.eval(dict(zip(s, xv)))
        # Reset A and W
        A[n_pre, n_post] = A_init
        W[n_pre, n_post] = W_init
        return ll

    def _glm_ll_noA(self, n_pre, n_post, x, I_bias, I_stim, I_imp):
        """ Compute the log likelihood of the GLM with A=True and given W
        """
        # Set A in state dict x
        A = x['net']['graph']['A']
        A_init = A[n_pre, n_post]
        A[n_pre, n_post] = 0

        # Get the likelihood of the GLM under A and W
        s = [self.network.graph.A] + \
             _flatten(self.syms['net']['weights']) + \
            [self.glm.n,
             self.glm.bias_model.I_bias,
             self.glm.bkgd_model.I_stim,
             self.glm.imp_model.I_imp] + \
            _flatten(self.syms['glm']['nlin'])

        xv = [A] + \
             _flatten(x['net']['weights']) + \
             [n_post,
              I_bias,
              I_stim,
              I_imp] + \
            _flatten(x['glms'][n_post]['nlin'])

        ll = self.glm.ll.eval(dict(zip(s, xv)))
        A[n_pre, n_post] = A_init

        return ll

    def _collapsed_sample_AW(self, n_pre, n_post, x,
                             I_bias, I_stim, I_imp, p_A):
        """
        Do collapsed Gibbs sampling for an entry A_{n,n'} and W_{n,n'} where
        n = n_pre and n' = n_post.
        """
        # Set sigma_w and mu_w
        if n_pre == n_post:
            mu_w = self.mu_w_ref
            sigma_w = self.sigma_w_ref
        else:
            mu_w = self.mu_w
            sigma_w = self.sigma_w

        A = x['net']['graph']['A']
        W = x['net']['weights']['W'].reshape(A.shape)

        # Propose from the prior and see if A would change.
        prior_lp_A = np.log(p_A[n_pre, n_post])
        prior_lp_noA = np.log(1.0-p_A[n_pre, n_post])

        # Approximate G = \int_0^\infty p({s,c} | A, W) p(W_{n,n'}) dW_{n,n'}
        log_L = np.zeros(self.DEG_GAUSS_HERMITE)
        weighted_log_L = np.zeros(self.DEG_GAUSS_HERMITE)
        W_nns = np.sqrt(2) * sigma_w * self.GAUSS_HERMITE_ABSCISSAE + mu_w
        for i in np.arange(self.DEG_GAUSS_HERMITE):
            w = self.GAUSS_HERMITE_WEIGHTS[i]
            W_nn = W_nns[i]
            log_L[i] = self._glm_ll_A(n_pre, n_post, W_nn,
                                      x, I_bias, I_stim, I_imp)

            # Handle NaNs in the GLM log likelihood
            if np.isnan(weighted_log_L[i]):
                weighted_log_L[i] = -np.Inf

            weighted_log_L[i] = log_L[i] + np.log(w/np.sqrt(np.pi))

            # Handle NaNs in the GLM log likelihood
            if np.isnan(weighted_log_L[i]):
                weighted_log_L[i] = -np.Inf

        # compute log pr(A_nn) and log pr(\neg A_nn) via log G
        from scipy.misc import logsumexp
        log_G = logsumexp(weighted_log_L)
        if not np.isfinite(log_G):
            import pdb; pdb.set_trace()

        # Compute log Pr(A_nn=1) given prior and estimate of log lkhd after integrating out W
        log_pr_A = prior_lp_A + log_G
        # Compute log Pr(A_nn = 0 | {s,c}) = log Pr({s,c} | A_nn = 0) + log Pr(A_nn = 0)
        log_pr_noA = prior_lp_noA + \
                     self._glm_ll_noA(n_pre, n_post, x,
                                      I_bias, I_stim, I_imp)
        if np.isnan(log_pr_noA):
            log_pr_noA = -np.Inf

        # Sample A
        try:
            A[n_pre, n_post] = log_sum_exp_sample([log_pr_noA, log_pr_A])
        except Exception as e:
            import pdb; pdb.set_trace()
            raise e
            # import pdb; pdb.set_trace()
        set_vars('A', x['net']['graph'], A)

        # Sample W from its posterior, i.e. log_L with denominator log_G
        # If A_nn = 0, we don't actually need to resample W since it has no effect
        if A[n_pre,n_post] == 1:
            W_centers = np.concatenate(([mu_w-6.0*sigma_w],
                                        (W_nns[1:]+W_nns[:-1])/2.0,
                                        [mu_w+6.0*sigma_w]))
            W_bin_sizes = W_nns[1:]-W_nns[:-1]

            log_prior_W = -0.5/sigma_w**2 * (W_nns-mu_w)**2
            log_posterior_W = log_prior_W + log_L
            log_p_W = log_posterior_W - logsumexp(log_posterior_W)

            # Approximate the posterior at the W_centers by averaging
            log_posterior_avg = np.array([logsumexp(log_posterior_W[i:i+2]) for \
                                          i in range(self.DEG_GAUSS_HERMITE-1)]) \
                                - np.log(2.0)

            log_posterior_mass = log_posterior_avg + np.log(W_bin_sizes)

            # Normalize the posterior mass
            log_posterior_mass -= logsumexp(log_posterior_mass)

            # Compute the log CDF
            log_F_W = np.array([-np.Inf] +
                               [logsumexp(log_posterior_mass[:i]) for i in range(1,self.DEG_GAUSS_HERMITE)] +
                               [0.0])

            # Compute the log CDF
            # log_F_W = np.array([logsumexp(log_p_W[:i]) for i in range(1,self.DEG_GAUSS_HERMITE)] + [0])

            # Sample via inverse CDF. Since log is concave, this overestimates.
            W[n_pre, n_post] = np.interp(np.log(np.random.rand()),
                                         log_F_W,
                                         W_centers)

            assert np.isfinite(self._glm_ll_A(n_pre, n_post, W[n_pre, n_post], x, I_bias, I_stim, I_imp))

            # if n_pre==n_post:
            #     import pdb; pdb.set_trace()
        else:
            # Sample W from the prior
            W[n_pre, n_post] = mu_w + sigma_w * np.random.randn()

        # Set W in state dict x
        x['net']['weights']['W'] = W.ravel()

    def _slice_sample_W(self, n_pre, n_post, x, W_nns, lp_W_nns, I_bias, I_stim, I_imp):
        """
        Use slice sampling to choose the next W
        """
        # Set sigma_w and mu_w
        if n_pre == n_post:
            mu_w = self.mu_w_ref
            sigma_w = self.sigma_w_ref
        else:
            mu_w = self.mu_w
            sigma_w = self.sigma_w

        lp_fn = lambda w: self._glm_ll_A(n_pre, n_post, w, x, I_bias, I_stim, I_imp) \
                          -0.5/sigma_w**2 * (w-mu_w)**2

        # Randomly choose a height in [0, p(curr_W)]
        A = x['net']['graph']['A']
        W = x['net']['weights']['W'].reshape(A.shape)
        W_curr = W[n_pre, n_post]
        lp_curr = lp_fn(W_curr)
        h = lp_curr + np.log(np.random.rand())

        # Find W_nns with lp > h
        valid_W_nns = W_nns[lp_W_nns>h]
        if len(valid_W_nns) > 0:
            lb = np.amin(valid_W_nns)
            lb = np.minimum(lb, W_curr)
            ub = np.amax(valid_W_nns)
            ub = np.maximum(ub, W_curr)
        else:
            lb = None
            ub = None

        from inference.slicesample import slicesample
        W_next, _ = slicesample(W_curr.reshape((1,)), lp_fn, last_llh=lp_curr, step=sigma_w/10.0, x_l=lb, x_r=ub)
        return W_next[0]

    def _collapsed_sample_AW_with_prior(self, n_pre, n_post, x,
                                        I_bias, I_stim, I_imp, p_A):
        """
        Do collapsed Gibbs sampling for an entry A_{n,n'} and W_{n,n'} where
        n = n_pre and n' = n_post.
        """
        # Set sigma_w and mu_w
        if n_pre == n_post:
            mu_w = self.mu_w_ref
            sigma_w = self.sigma_w_ref
        else:
            mu_w = self.mu_w
            sigma_w = self.sigma_w

        A = x['net']['graph']['A']
        W = x['net']['weights']['W'].reshape(A.shape)

        # Propose from the prior and see if A would change.
        prior_lp_A = np.log(p_A[n_pre, n_post])
        prop_A = np.int8(np.log(np.random.rand()) < prior_lp_A)

        # We only need to compute the acceptance probability if the proposal
        # would change A
        A_init = A[n_pre, n_post]
        W_init = W[n_pre, n_post]
        if A[n_pre, n_post] != prop_A:

            # Approximate G = \int_0^\infty p({s,c} | A, W) p(W_{n,n'}) dW_{n,n'}
            log_L = np.zeros(self.DEG_GAUSS_HERMITE)
            W_nns = np.sqrt(2) * sigma_w * self.GAUSS_HERMITE_ABSCISSAE + mu_w
            for i in np.arange(self.DEG_GAUSS_HERMITE):
                w = self.GAUSS_HERMITE_WEIGHTS[i]
                W_nn = W_nns[i]
                log_L[i] = np.log(w/np.sqrt(np.pi)) + \
                           self._glm_ll_A(n_pre, n_post, W_nn,
                                          x, I_bias, I_stim, I_imp)

                # Handle NaNs in the GLM log likelihood
                if np.isnan(log_L[i]):
                    log_L[i] = -np.Inf

            # compute log pr(A_nn) and log pr(\neg A_nn) via log G
            from scipy.misc import logsumexp
            log_G = logsumexp(log_L)

            # Compute log Pr(A_nn=1) given prior and estimate of log lkhd after integrating out W
            log_lkhd_A = log_G
            # Compute log Pr(A_nn = 0 | {s,c}) = log Pr({s,c} | A_nn = 0) + log Pr(A_nn = 0)
            log_lkhd_noA = self._glm_ll_noA(n_pre, n_post, x, I_bias, I_stim, I_imp)

            # Decide whether or not to accept
            log_pr_accept = log_lkhd_A - log_lkhd_noA if prop_A else log_lkhd_noA - log_lkhd_A
            if np.log(np.random.rand()) < log_pr_accept:
                # Update A
                A[n_pre, n_post] = prop_A

                # Update W if there is an edge in A
                if A[n_pre, n_post]:
                    # Update W if there is an edge
                    log_p_W = log_L - log_G
                    # Compute the log CDF
                    log_F_W = [logsumexp(log_p_W[:i]) for i in range(1,self.DEG_GAUSS_HERMITE)] + [0]
                    # Sample via inverse CDF
                    W[n_pre, n_post] = np.interp(np.log(np.random.rand()),
                                                 log_F_W,
                                             W_nns)

        elif A[n_pre, n_post]:
            assert A[n_pre, n_post] == A_init
            # If we propose not to change A then we accept with probability 1, but we
            # still need to update W
            # Approximate G = \int_0^\infty p({s,c} | A, W) p(W_{n,n'}) dW_{n,n'}
            log_L = np.zeros(self.DEG_GAUSS_HERMITE)
            W_nns = np.sqrt(2) * sigma_w * self.GAUSS_HERMITE_ABSCISSAE + mu_w
            for i in np.arange(self.DEG_GAUSS_HERMITE):
                w = self.GAUSS_HERMITE_WEIGHTS[i]
                W_nn = W_nns[i]
                log_L[i] = np.log(w/np.sqrt(np.pi)) + \
                           self._glm_ll_A(n_pre, n_post, W_nn,
                                          x, I_bias, I_stim, I_imp)

                # Handle NaNs in the GLM log likelihood
                if np.isnan(log_L[i]):
                    log_L[i] = -np.Inf

            # compute log pr(A_nn) and log pr(\neg A_nn) via log G
            from scipy.misc import logsumexp
            log_G = logsumexp(log_L)

            # Update W if there is an edge
            log_p_W = log_L - log_G
            # Compute the log CDF
            log_F_W = [logsumexp(log_p_W[:i]) for i in range(1,self.DEG_GAUSS_HERMITE)] + [0]
            # Sample via inverse CDF
            W[n_pre, n_post] = np.interp(np.log(np.random.rand()),
                                         log_F_W,
                                         W_nns)

        # Set W in state dict x
        x['net']['weights']['W'] = W.ravel()

    def update(self, x, n):
        """ Collapsed Gibbs sample a column of A and W
        """
        A = x['net']['graph']['A']
        N = A.shape[0]
        I_bias, I_stim, I_imp, p_A = self._precompute_vars(x, n)

        order = np.arange(N)
        np.random.shuffle(order)
        for n_pre in order:
            # print "Sampling %d->%d" % (n_pre, n_post)
            if self.propose_from_prior:
                self._collapsed_sample_AW_with_prior(n_pre, n, x,
                                                     I_bias, I_stim, I_imp, p_A)
            else:
                self._collapsed_sample_AW(n_pre, n, x,
                                          I_bias, I_stim, I_imp, p_A)
        return x

class GibbsNetworkColumnUpdate(ParallelMetropolisHastingsUpdate):

    def __init__(self):
        super(GibbsNetworkColumnUpdate, self).__init__()

        self.avg_accept_rate = 0.9
        self.step_sz = 0.05

    def preprocess(self, population):
        """ Initialize functions that compute the gradient and Hessian of
            the log probability with respect to the differentiable network
            parameters, e.g. the weight matrix if it exists.
        """
        self.N = population.model['N']
        self.population = population
        self.network = population.network
        self.glm = population.glm
        self.syms = population.get_variables()

        self.g_netlp_wrt_W = T.grad(self.network.log_p, self.syms['net']['weights']['W'])
        self.g_glmlp_wrt_W = T.grad(self.glm.ll, self.syms['net']['weights']['W'])


    def _precompute_currents(self, x, n_post):
        """ Precompute currents for sampling A and W
        """
        nvars = self.population.extract_vars(x, n_post)

        I_bias = seval(self.glm.bias_model.I_bias,
                       self.syms,
                       nvars)

        I_stim = seval(self.glm.bkgd_model.I_stim,
                       self.syms,
                       nvars)

        I_imp = seval(self.glm.imp_model.I_imp,
                      self.syms,
                      nvars)

        return I_bias, I_stim, I_imp

    def _lp_A(self, A, x, n_post, I_bias, I_stim, I_imp):
        """ Compute the log probability for a given column A[:,n_post]
        """
        # Set A in state dict x
        set_vars('A', x['net']['graph'], A)

        # Get the prior probability of A
        lp = seval(self.network.log_p,
                   self.syms['net'],
                   x['net'])

        # Get the likelihood of the GLM under A
        s = [self.network.graph.A] + \
             _flatten(self.syms['net']['weights']) + \
            [self.glm.n,
             self.glm.bias_model.I_bias,
             self.glm.bkgd_model.I_stim,
             self.glm.imp_model.I_imp] + \
            _flatten(self.syms['glm']['nlin'])

        xv = [A] + \
             _flatten(x['net']['weights']) + \
             [n_post,
              I_bias,
              I_stim,
              I_imp] + \
            _flatten(x['glms'][n_post]['nlin'])

        lp += self.glm.ll.eval(dict(zip(s, xv)))

        return lp

    # Helper functions to sample W
    def _lp_W(self, W, x, n_post, I_bias, I_stim, I_imp):
        """ Compute the log probability for a given column W[:,n_post]
        """
        # Set A in state dict x
        set_vars('W', x['net']['weights'], W)

        # Get the prior probability of A
        lp = seval(self.network.log_p,
                   self.syms['net'],
                   x['net'])

        # Get the likelihood of the GLM under W
        s = _flatten(self.syms['net']['graph']) + \
            [self.network.weights.W_flat,
             self.glm.n,
             self.glm.bias_model.I_bias,
             self.glm.bkgd_model.I_stim,
             self.glm.imp_model.I_imp] + \
            _flatten(self.syms['glm']['nlin'])

        xv = _flatten(x['net']['graph']) + \
             [W,
              n_post,
              I_bias,
              I_stim,
              I_imp] + \
             _flatten(x['glms'][n_post]['nlin'])

        lp += self.glm.ll.eval(dict(zip(s, xv)))

        return lp

    def _grad_lp_W(self, W, x, n_post, I_bias, I_stim, I_imp):
        """ Compute the log probability for a given column W[:,n_post]
        """
        # Set A in state dict x
        set_vars('W', x['net']['weights'], W)

        # Get the prior probability of A
        g_lp = seval(self.g_netlp_wrt_W,
                     self.syms['net'],
                     x['net'])

        # Get the likelihood of the GLM under W
        s = _flatten(self.syms['net']['graph']) + \
            [self.network.weights.W_flat,
             self.glm.n,
             self.glm.bias_model.I_bias,
             self.glm.bkgd_model.I_stim,
             self.glm.imp_model.I_imp] + \
            _flatten(self.syms['glm']['nlin'])

        xv = _flatten(x['net']['graph']) + \
             [W,
              n_post,
              I_bias,
              I_stim,
              I_imp] + \
             _flatten(x['glms'][n_post]['nlin'])

        g_lp += seval(self.g_glmlp_wrt_W,
                      dict(zip(range(len(s)), s)),
                      dict(zip(range(len(xv)),xv)))
        # g_lp += self.g_glmlp_wrt_W.eval(dict(zip(s, xv)))

        # Ignore gradients wrt columns other than n_post
        g_mask = np.zeros((self.N,self.N))
        g_mask[:,n_post] = 1
        g_lp *= g_mask.flatten()
        return g_lp

    def _sample_column_of_A(self, n_post, x, I_bias, I_stim, I_imp):
        # Sample the adjacency matrix if it exists
        if 'A' in x['net']['graph']:
            # print "Sampling A"
            A = x['net']['graph']['A']
            N = A.shape[0]

            # Sample coupling filters from other neurons
            for n_pre in np.arange(N):
                # print "Sampling A[%d,%d]" % (n_pre,n_post)
                # WARNING Setting A is somewhat of a hack. It only works
                # because nvars copies x's pointer to A rather than making
                # a deep copy of the adjacency matrix.
                A[n_pre,n_post] = 0
                log_pr_noA = self._lp_A(A, x, n_post, I_bias, I_stim, I_imp)

                A[n_pre,n_post] = 1
                log_pr_A = self._lp_A(A, x, n_post, I_bias, I_stim, I_imp)

                # Sample A[n_pre,n_post]
                A[n_pre,n_post] = log_sum_exp_sample([log_pr_noA, log_pr_A])

                if not np.isfinite(log_pr_noA) or not np.isfinite(log_pr_A):
                    import pdb; pdb.set_trace()

                if n_pre == n_post and not A[n_pre, n_post]:
                    import pdb; pdb.set_trace()

    def _sample_column_of_W(self, n_post, x, I_bias, I_stim, I_imp):
        # Sample W if it exists
        if 'W' in x['net']['weights']:
            # print "Sampling W"
            nll = lambda W: -1.0 * self._lp_W(W, x, n_post, I_bias, I_stim, I_imp)
            grad_nll = lambda W: -1.0 * self._grad_lp_W(W, x, n_post, I_bias, I_stim, I_imp)

            # Automatically tune these parameters
            n_steps = 10
            (W, new_step_sz, new_accept_rate) = hmc(nll,
                                                    grad_nll,
                                                    self.step_sz,
                                                    n_steps,
                                                    x['net']['weights']['W'],
                                                    adaptive_step_sz=True,
                                                    avg_accept_rate=self.avg_accept_rate)

            # Update step size and accept rate
            self.step_sz = new_step_sz
            self.avg_accept_rate = new_accept_rate
            # print "W step sz: %.3f\tW_accept rate: %.3f" % (new_step_sz, new_accept_rate)

            # Update current W
            x['net']['weights']['W'] = W

    def update(self, x, n):
        """ Sample a single column of the network (all the incoming
            coupling filters). This is a parallelizable chunk.
        """
        # Precompute the filtered currents from other GLMs
        I_bias, I_stim, I_imp = self._precompute_currents(x, n)
        self._sample_column_of_A(n, x, I_bias, I_stim, I_imp)
        self._sample_column_of_W(n, x, I_bias, I_stim, I_imp)
        return x


class LatentDistanceNetworkUpdate(MetropolisHastingsUpdate):
    """
    Gibbs sample the parameters of a latent distance model, namely the
    latent locations (if they are not given) and the distance scale.
    """
    def __init__(self):
        super(LatentDistanceNetworkUpdate, self).__init__()

        self.avg_accept_rate = 0.9
        self.step_sz = 0.05

    def preprocess(self, population):
        # Compute the log probability of the graph and
        # of the locations under the prior, as well as its
        # gradient
        self.N = population.model['N']
        self.network = population.network
        self.syms = population.get_variables()

        self.L_shape = population.sample()['net']['graph']['L'].shape

        self.g_netlp_wrt_L = T.grad(self.network.graph.log_p,
                                    self.syms['net']['graph']['L'])

    def _lp_L(self, L, x):
        # Set L in state dict x
        set_vars('L', x['net']['graph'], L)

        # Get the prior probability of A
        lp = seval(self.network.graph.log_p, self.syms['net']['graph'], x['net']['graph'])
        return lp


    def _grad_lp_wrt_L(self, L, x):
        # Set L in state dict x
        set_vars('L', x['net']['graph'], L)

        # Get the grad of the log prob of A
        g_lp = seval(self.g_netlp_wrt_L,
                     self.syms['net']['graph'],
                     x['net']['graph'])
        return g_lp

    def update(self, x):
        """
        Sample L using HMC given A and delta (distance scale)
        """
        # Sample W if it exists
        if 'L' in x['net']['graph']:
            # print "Sampling W"
            nll = lambda L: -1.0 * self._lp_L(L.reshape(self.L_shape), x)
            grad_nll = lambda L: -1.0 * self._grad_lp_wrt_L(L.reshape(self.L_shape), x).ravel()

            # Automatically tune these paramseters
            n_steps = 10
            (L, new_step_sz, new_accept_rate) = hmc(nll,
                                                    grad_nll,
                                                    self.step_sz,
                                                    n_steps,
                                                    x['net']['graph']['L'].ravel(),
                                                    adaptive_step_sz=True,
                                                    avg_accept_rate=self.avg_accept_rate)

            # Update step size and accept rate
            self.step_sz = new_step_sz
            self.avg_accept_rate = new_accept_rate

            # Update current L
            x['net']['graph']['L'] = L.reshape(self.L_shape)

        return x

def initialize_updates(population):
    """ Compute the set of updates required for the given population.
        TODO: Figure out how to do this in a really principled way.
    """
    serial_updates = []
    parallel_updates = []
    # All populations have a parallel GLM sampler
    print "Initializing GLM sampler"
    glm_sampler = HmcGlmUpdate()
    glm_sampler.preprocess(population)
    parallel_updates.append(glm_sampler)

    # All populations have a network sampler
    # TODO: Decide between collapsed and standard Gibbs
    print "Initializing network sampler"
    # net_sampler = GibbsNetworkColumnUpdate()
    net_sampler = CollapsedGibbsNetworkColumnUpdate()
    net_sampler.preprocess(population)
    parallel_updates.append(net_sampler)

    # If the graph model is a latent distance model, add its update
    from components.graph import LatentDistanceGraphModel
    if isinstance(population.network.graph, LatentDistanceGraphModel):
        loc_sampler = LatentDistanceNetworkUpdate()
        loc_sampler.preprocess(population)
        serial_updates.append(loc_sampler)

    return serial_updates, parallel_updates

def gibbs_sample(population, 
                 data, 
                 N_samples=1000,
                 x0=None, 
                 init_from_mle=True):
    """
    Sample the posterior distribution over parameters using MCMC.
    """
    N = population.model['N']

    # Draw initial state from prior if not given
    if x0 is None:
        x0 = population.sample()
        
        if init_from_mle:
            print "Initializing with coordinate descent"
            from models.model_factory import make_model, convert_model
            from population import Population
            mle_model = make_model('standard_glm', N=N)
            mle_popn = Population(mle_model)
            mle_popn.set_data(data)
            mle_x0 = mle_popn.sample()

            # Initialize with MLE under standard GLM
            mle_x0 = coord_descent(mle_popn, data, x0=mle_x0, maxiter=1)

            # Convert between inferred parameters of the standard GLM
            # and the parameters of this model. Eg. Convert unweighted 
            # networks to weighted networks with normalized impulse responses.
            x0 = convert_model(mle_popn, mle_model, mle_x0, population, population.model, x0)

    # Create updates for this population
    serial_updates, parallel_updates = initialize_updates(population)

    # DEBUG Profile the Gibbs sampling loop
    import cProfile, pstats, StringIO
    pr = cProfile.Profile()
    pr.enable()

    # Alternate fitting the network and fitting the GLMs
    x_smpls = [x0]
    x = x0

    import time
    start_time = time.clock()

    for smpl in np.arange(N_samples):
        # Print the current log likelihood
        lp = population.compute_log_p(x)

        # Compute iters per second
        stop_time = time.clock()
        if stop_time - start_time == 0:
            print "Gibbs iteration %d. Iter/s exceeds time resolution. Log prob: %.3f" % (smpl, lp)
        else:
            print "Gibbs iteration %d. Iter/s = %f. Log prob: %.3f" % (smpl,
                                                                       1.0/(stop_time-start_time),
                                                                       lp)
        start_time = stop_time

        # Go through each parallel MH update
        for parallel_update in parallel_updates:
            for n in np.arange(N):
                parallel_update.update(x, n)

        # Sample the serial updates
        for serial_update in serial_updates:
            serial_update.update(x)

        x_smpls.append(copy.deepcopy(x))

    pr.disable()
    s = StringIO.StringIO()
    sortby = 'cumulative'
    ps = pstats.Stats(pr, stream=s).sort_stats(sortby)
    ps.print_stats()

    with open('mcmc.prof.txt', 'w') as f:
        f.write(s.getvalue())
        f.close()

    return x_smpls
