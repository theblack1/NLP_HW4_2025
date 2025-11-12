# import transformers, datasets, evaluate, sklearn, nltk

# import sys
# print("py:", sys.version)
# try:
#     import numpy as np
#     print("numpy OK:", np.__version__, np.__file__)
# except Exception as e:
#     print("numpy import FAILED:", repr(e))

# import nltk
# import numpy, scipy, sklearn, nltk
# print("numpy:", numpy.__version__)
# print("scipy:", scipy.__version__)
# print("sklearn:", sklearn.__version__)
# from nltk.classify.scikitlearn import SklearnClassifier
# print("NLTK + scikit-learn OK")


# import nltk
# nltk.download('wordnet')
# nltk.download('punkt')

# Verify PyTorch installation with CUDA
# import torch
# print("torch:", torch.__version__)
# print("cuda available:", torch.cuda.is_available())
# if torch.cuda.is_available():
#     print("device:", torch.cuda.get_device_name(0))


import torch, numpy as np, nltk, sklearn, datasets, transformers, evaluate
print("torch:", torch.__version__, "CUDA:", torch.cuda.is_available())
print("numpy:", np.__version__)
print("nltk:", nltk.__version__)
print("sklearn:", sklearn.__version__)
print("datasets:", datasets.__version__)
print("transformers:", transformers.__version__)
print("evaluate:", evaluate.__version__)

# import os, sys
# print("CWD:", os.getcwd())
# print("sys.path[0]:", sys.path[0])
# print("List CWD:", [f for f in os.listdir('.') if f.lower().startswith(('torch','numpy','nltk'))])

# import numpy as np
# print("NumPy:", np.__version__, np.__file__)

# import pyarrow as pa, sys
# print("pyarrow:", pa.__version__)

import datasets, fsspec
print('datasets', datasets.__version__, 'fsspec', fsspec.__version__)
