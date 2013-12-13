"""
Make models from a template
"""
from standard_glm import StandardGlm
from spatiotemporal_glm import SpatiotemporalGlm
from simple_weighted_model import SimpleWeightedModel
from simple_sparse_model import SimpleSparseModel


import copy

def make_model(template, N=None):
    """ Construct a model from a template and update the specified parameters
    """
    if isinstance(template, str):
        # Create the specified model
        if template.lower() == 'standard_glm' or \
           template.lower() == 'standardglm':
            model = copy.deepcopy(StandardGlm)
        elif template.lower() == 'spatiotemporal_glm':
            model = copy.deepcopy(SpatiotemporalGlm)
        elif template.lower() == 'simple_weighted_model' or \
             template.lower() == 'simpleweightedmodel':
            model = copy.deepcopy(SimpleWeightedModel)
        elif template.lower() == 'simple_sparse_model' or \
             template.lower() == 'simplesparsemodel':
            model = copy.deepcopy(SimpleSparseModel)


    elif isinstance(template, dict):
        model = copy.deepcopy(template)
    else:
        raise Exception("Unrecognized template model!")

    # Override template model parameters
    if N is not None:
        model['N'] = N

    # TODO Update other parameters as necessary

    return model
