#python -m test.generate_synth_data -N 4 -T 60 -m sparse_weighted_model -r data
python -m test.synth_map -d data.pkl -m sparse_weighted_model  -r results
