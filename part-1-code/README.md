## Set up environment
We recommend you to set up a conda environment for packages used in this homework.
```
conda create -n hw4-part-1-nlp python=3.9 -y
conda activate hw4-part-1-nlp
python -m pip install -U pip setuptools wheel
pip install torch==1.13.1+cu117 torchvision==0.14.1+cu117 torchaudio==0.13.1 --extra-index-url https://download.pytorch.org/whl/cu117
conda install -c conda-forge transformers==4.26.1 datasets==2.9.0 evaluate==0.4.0 scikit-learn==1.2.1 nltk==3.8.1 -y
conda install -y -c conda-forge vs2015_runtime
```

After this, you will need to install certain packages in nltk
```
python
>>> import nltk
>>> nltk.download('wordnet')
>>> nltk.download('punkt')
>>> exit()
```
