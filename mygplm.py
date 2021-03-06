import cPickle
#import scipy.io
#import numpy as np
#import os

#from population import Population
#from models.model_factory import *
#from plotting.plot_results import plot_results
#from utils.theano_func_wrapper import seval #theano not working on phone
from utils.io import parse_cmd_line_args, load_data


# Load data from file or create synthetic test dataset
data = load_data(options)

print "Creating master population object"
model = make_model(options.model, N=data['N'])
popn = Population(model)
popn.set_data(data) 
    
# Initialize the GLM with the data
popn_true = None
x_true = None
if 'vars' in data:
    x_true = data['vars']
    
    # Load the true model 
    model_true = None
    data_dir = os.path.dirname(options.dataFile)
    model_file = os.path.join(data_dir, 'model.pkl')
    print "Loading true model from %s" % model_file
    with open(model_file) as f:
        model_true = cPickle.load(f)
        # HACK FOR EXISTING DATA!
        if 'N_dims' not in model_true['network']['graph']:
            model_true['network']['graph']['N_dims'] = 1
            if 'location_prior' not in model_true['network']['graph']:
                model_true['network']['graph']['location_prior'] = \
                    {
                    'type' : 'gaussian',
                    'mu' : 0.0,
                    'sigma' : 1.0
                    }
        if 'L' in x_true['net']['graph']:
            x_true['net']['graph']['L'] = x_true['net']['graph']['L'].ravel()
            # END HACK
            popn_true = Population(model_true)
            popn_true.set_data(data)
            ll_true = popn_true.compute_log_p(x_true)
            print "true LL: %f" % ll_true
            

                    
