""" Plot the inference results
"""
import os

import numpy as np
import matplotlib
matplotlib.use('Agg')       # To enable saving remotely
import matplotlib.pyplot as plt
import matplotlib.cm as cm

# Get a red-gray cmap
cmap = cm.get_cmap('RdGy')

from utils.avg_dicts import average_list_of_dicts, std_list_of_dicts

import cPickle

def plot_connectivity_matrix(s_inf, s_true=None):
    W_inf = s_inf['net']['weights']['W'] * s_inf['net']['graph']['A']

    true_given = s_true is not None
    if true_given:
        plt.subplot(1,2,1)
        W_true = s_true['net']['weights']['W'] * s_true['net']['graph']['A']
        W_max = np.amax(np.maximum(np.abs(W_true),np.abs(W_inf)))
    else:
        W_max = np.amax(np.abs(W_inf))
        
    px_per_node = 10

    if true_given:
        plt.imshow(np.kron(W_true,np.ones((px_per_node,px_per_node))),
                   vmin=-W_max,vmax=W_max,
                   extent=[0,1,0,1],
                   interpolation='nearest')
        plt.colorbar()
        plt.title('True Network')
        plt.subplot(1,2,2)

    # Plot the inferred network
    plt.imshow(np.kron(W_inf,np.ones((px_per_node,px_per_node))),
               vmin=-W_max,vmax=W_max,
               extent=[0,1,0,1],
               interpolation='nearest')
    plt.colorbar()
    plt.title('Inferred Network')
    
def plot_stim_response(s_glm, s_glm_std=None, color=None):
    if 'stim_response_t' in s_glm['bkgd'].keys() and \
       'stim_response_x' in s_glm['bkgd'].keys():
      
        # Get the stimulus responses
        stim_x = s_glm['bkgd']['stim_response_x']
        stim_t = s_glm['bkgd']['stim_response_t']
            
        plt.subplot(1,2,1)
        plt.plot(stim_x, color=color, linestyle='-')
        plt.hold(True)
        # If standard deviation is given, plot that as well
        if s_glm_std is not None:
            stim_x_std = s_glm_std['bkgd']['stim_response_x']
            plt.plot(stim_x + 2*stim_x_std, color=color, linestyle='--') 
            plt.plot(stim_x - 2*stim_x_std, color=color, linestyle='--')
            
        plt.subplot(1,2,2)
        plt.plot(opt_stim_t,'-r')
        plt.hold(True)
        if s_glm_std is not None:
            stim_t_std = s_glm_std['bkgd']['stim_response_t']
            plt.plot(stim_t + 2*stim_t_std, color=color, linestyle='--') 
            plt.plot(stim_t - 2*stim_t_std, color=color, linestyle='--')
            
    elif 'stim_response' in s_glm['bkgd'].keys():
        stim_t = s_glm['bkgd']['stim_response']
        plt.plot(stim_t, color=color, linestyle='-')
        plt.hold(True)
        if s_glm_std is not None:
            stim_t_std = s_glm_std['bkgd']['stim_response']
            plt.plot(stim_t + 2*stim_t_std, color=color, linestyle='--') 
            plt.plot(stim_t - 2*stim_t_std, color=color, linestyle='--')

def plot_imp_responses(s_inf, s_std=None, color=None):
    """ Plot the impulse responses plus or minus two standard devs
    """ 
    # Get a red-gray cmap
    cmap = cm.get_cmap('RdGy')

    # Get the weights of the impulse responses
    W_inf = s_inf['net']['weights']['W'] * s_inf['net']['graph']['A']
    N = W_inf.shape[0]
    
    s_imps = []
    s_imps_std = []
    for n_post in np.arange(N):
        s_imp_row = []
        s_imp_std_row = []
        
        s_imp_n = s_inf['glms'][n_post]['imp']['impulse']
        if s_std is not None:
            s_imp_std_n = s_std['glms'][n_post]['imp']['impulse']

        for n_pre in np.arange(N):
            w = W_inf[n_pre,n_post]
            s_imp_row.append(w*s_imp_n[n_pre,:])
            if s_std is not None:
                s_imp_std_row.append(w*s_imp_std_n[n_pre,:])

        s_imps.append(s_imp_row)
        s_imps_std.append(s_imp_std_row)
        
    s_imps = np.array(s_imps)    
    s_imps_std = np.array(s_imps_std)
    
    # Transpose so that pre is row index 
    s_imps = np.transpose(s_imps, [1,0,2])
    if s_std is not None:
        s_imps_std = np.transpose(s_imps_std, [1,0,2])
    else:
        s_imps_std = np.zeros_like(s_imps)
    imp_max = np.amax(np.abs(s_imps+2*s_imps_std))
        
    W_imp = np.sum(s_imps,2)
    W_imp_max = np.amax(W_imp)
    for n_pre in np.arange(N):
        for n_post in np.arange(N):
            # Set background color based on weight of impulse
            bkgd_color = cmap((W_imp[n_pre,n_post] -(-W_imp_max))/(2*W_imp_max))
            # Set it slightly transparent
            tcolor = list(bkgd_color)
            tcolor[3] = 0.75
            tcolor = tuple(tcolor)
            plt.subplot(N,N,n_pre*N+n_post + 1, axisbg=tcolor)

            # Plot the inferred impulse response
            plt.plot(np.squeeze(s_imps[n_pre,n_post,:]),color=color, linestyle='-')
            plt.hold(True)
            
            # Plot plus or minus 2 stds
            if s_std is not None:
                plt.plot(np.squeeze(s_imps[n_pre,n_post,:] +\
                                    2*s_imps_std[n_pre,n_post,:]),
                                    color=color, 
                                    linestyle='--')
                plt.plot(np.squeeze(s_imps[n_pre,n_post,:] -\
                                    2*s_imps_std[n_pre,n_post,:]),
                                    color=color, 
                                    linestyle='--')

            plt.plot(np.zeros_like(np.squeeze(s_imps[n_pre,n_post,:])),
                     color='k', linestyle=':')

            # Set labels 
            plt.xlabel("")
            plt.xticks([])
            plt.yticks([])
            plt.ylabel("")
            plt.ylim(-imp_max,imp_max)

def plot_firing_rate(s_glm, s_glm_std=None, color=None):
    plt.plot(s_glm['lam'],
             color=color)
    plt.hold(True)
    
    if s_glm_std is not None:
        plt.plot(s_glm['lam'] + 2*s_glm_std['lam'],
                 color=color,
                 linestyle='--')
        plt.plot(s_glm['lam'] - 2*s_glm_std['lam'],
                 color=color,
                 linestyle='--')
                
def plot_results(population, x_inf, x_true=None, resdir=None):
    """ Plot the inferred stimulus tuning curves and impulse responses
    """
    if not resdir:
        resdir = '.'


    true_given = x_true is not None
    
    # Make sure we have a list of x's
    if not isinstance(x_inf, list):
        x_inf = [x_inf]

    # Evaluate the state for each of the parameter settings
    N_samples = len(x_inf)
    s_inf = []
    for x in x_inf:
        s_inf.append(population.eval_state(x))
    
    s_true = None
    if true_given:
        s_true = population.eval_state(x_true)

    # Average the inferred states
    s_avg = average_list_of_dicts(s_inf)
    s_std = std_list_of_dicts(s_inf, s_avg)
    N = population.N

    # TODO Fix the averaging of W and A
    # E[W] * E[A] != E[W*A]
    # Plot the inferred connectivity matrix
    print "Plotting connectivity matrix"
    f = plt.figure()
    plot_connectivity_matrix(s_avg, s_true)
    f.savefig(os.path.join(resdir,'conn.pdf'))
    plt.close(f)

    # Plot stimulus response functions
    print "Plotting stimulus response functions"
    for n in range(N):
        f = plt.figure()
        plot_stim_response(s_avg['glms'][n], 
                           s_glm_std=s_std['glms'][n],
                           color='r')
        if true_given:
            plot_stim_response(s_true['glms'][n], 
                               color='k')
        
        f.savefig(os.path.join(resdir,'stim_resp_%d.pdf' % n))
        plt.close(f)
        
    # Plot the impulse responses
    f = plt.figure()
    plot_imp_responses(s_avg,
                       s_std,
                       color='r')
    if true_given:
        plot_imp_responses(s_true, color='k')

    f.savefig(os.path.join(resdir,'imp_resp.pdf'))
    plt.close(f)
    
    # Plot the firing rates
    print "Plotting firing rates"
    for n in range(N):
        f = plt.figure()
        plot_firing_rate(s_avg['glms'][n], 
                         s_std['glms'][n], 
                         color='r')
        if true_given:
            plot_firing_rate(s_true['glms'][n], color='k')

        # Plot the spike times
        St = np.nonzero(population.glm.S.get_value()[:,n])[0]
        plt.plot(St,0.1*np.ones_like(St),'kx')
        
        # Zoom in on small fraction
        plt.xlim([10000,12000])
        plt.title('Firing rate %d' % n)
            
        f.savefig(os.path.join(resdir,'firing_rate_%d.pdf' % n))
        plt.close(f)

def parse_cmd_line_args():
    """
    Parse command line parameters
    """
    from optparse import OptionParser

    parser = OptionParser()
    
    parser.add_option("-d", "--dataFile", dest="dataFile",
                      help="Data file to load")

    parser.add_option("-r", "--resultsFile", dest="resultsFile", default='.',
                      help="Results file to plot.")

    (options, args) = parser.parse_args()

    # Check if specified files exist
    if options.resultsFile is None or not os.path.exists(options.resultsFile):
        raise Exception("Invalid results file: %s" % options.resultsFile)

    return (options, args)


if __name__ == "__main__":
    # Parse command line args
    (options, args) = parse_cmd_line_args()

    # Load data                                                              
    if options.dataFile.endswith('.mat'):
        print "Loading data from %s" % options.dataFile
        import scipy.io
        data = scipy.io.loadmat(options.dataFile, squeeze_me=True, mat_dtype=True)
        data['N'] = np.int(data['N'])
    elif options.dataFile.endswith('.pkl'):
        print "Loading data from %s" % options.dataFile
        import cPickle
        with open(options.dataFile,'r') as f:
            data = cPickle.load(f)
    else:
        raise Exception("Unrecognized file type: %s" % options.dataFile)

    # Load results
    if options.resultsFile.endswith('.pkl'):
        print "Loading results from %s" % options.resultsFile
        with open(options.resultsFile,'r') as f:
            x_inf = cPickle.load(f)
    else:
        raise Exception("Unrecognized file type: %s" % options.resultsFile)

    print "Initializing GLM"
    from population import Population
    from models.rgc import Rgc
    model = Rgc
    population = Population(model)
    print "Conditioning on the data"
    population.set_data(data)

    print "Plotting results"
    plot_results(population, x_inf)
