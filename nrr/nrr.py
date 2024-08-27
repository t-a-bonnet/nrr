import numpy as np
import pandas as pd
import pyterrier as pt
import textdistance
import torch
import torch.nn as nn
import os
import shutil
import re
import string as st

from fuzzywuzzy import fuzz
from nltk import ngrams
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import StandardScaler
from unidecode import unidecode


def calculate_fuzzy_matching_score(query, text):
    return fuzz.token_set_ratio(query, text)

def calculate_smith_waterman_similarity(query, text):
    matrix = [[0] * (len(str(text)) + 1) for _ in range(len(str(query)) + 1)]
    max_score = 0
    max_i = 0
    max_j = 0
    for i in range(1, len(str(query)) + 1):
        for j in range(1, len(str(text)) + 1):
            match = 2 if str(query)[i - 1] == str(text)[j - 1] else -1
            score = max(
                matrix[i - 1][j - 1] + match,
                matrix[i - 1][j] - 1,
                matrix[i][j - 1] - 1,
                0
            )
            matrix[i][j] = score
            if score > max_score:
                max_score = score
                max_i = i
                max_j = j
    distance = max_score
    while max_i > 0 and max_j > 0 and matrix[max_i][max_j] != 0:
        if matrix[max_i][max_j] == matrix[max_i - 1][max_j - 1] + (2 if str(query)[max_i - 1] == str(text)[max_j - 1] else -1):
            max_i -= 1
            max_j -= 1
        elif matrix[max_i][max_j] == matrix[max_i - 1][max_j] - 1:
            max_i -= 1
        else:
            max_j -= 1
    return distance

def calculate_lcs(query, text):
    m = len(str(query))
    n = len(str(text))
    lcs_matrix = np.zeros((m+1, n+1))
    for i in range(1, m+1):
        for j in range(1, n+1):
            if str(query)[i-1] == str(text)[j-1]:
                lcs_matrix[i][j] = lcs_matrix[i-1][j-1] + 1
            else:
                lcs_matrix[i][j] = max(lcs_matrix[i-1][j], lcs_matrix[i][j-1])
    return lcs_matrix[m][n]

def preprocess_text(text):
    if not text or pd.isna(text):
        return None
    text = unidecode(text)
    text = ''.join([x if x.isalpha() or x.isspace() else ' ' for x in text])
    special_characters = "¶¬©£ª√®"
    characters_to_remove = st.punctuation + special_characters
    text = ''.join([x for x in text if x not in characters_to_remove])
    text = text.lower()
    text = text.strip()
    text = re.sub(r'\s+', ' ', text)
    return text

class NRR:
    def __init__(self, index_path, mlp_model_path='./nrr_model.pth'):
        # Initialize PyTerrier
        pt.init()

    def search(self, query_df, text_df):
        # Ensure queries are preprocessed
        query_df.dropna(subset=['query'], inplace=True)
        query_df['query'] = query_df['query'].apply(preprocess_text)
        query_df['qid'] = query_df.index + 1
        query_df.reset_index(drop=True, inplace=True)
        query_df['qid'] = query_df['qid'].astype(str)

        # Ensure texts are preprocessed
        text_df.dropna(subset=['text'], inplace=True)
        text_df['text'] = text_df['text'].apply(preprocess_text)
        text_df['docno'] = text_df.index + 1
        text_df.reset_index(drop=True, inplace=True)
        text_df['docno'] = text_df['docno'].astype(str)

        # Remove existing index directory
        index_path = './pd_index'
        if os.path.exists(index_path):
            shutil.rmtree(index_path)

        # Indexing texts
        pd_indexer = pt.DFIndexer(index_path)
        index = pd_indexer.index(text_df['text'], text_df['docno'])

        # Set up the retrieval model
        br = pt.BatchRetrieve(index, controls={'wmodel': 'LGD', 'max_results': 100})
        br.setControl('wmodel', 'LGD')

        def pyterrier_search_function(query_df, num_results, text_df):
            results_dict = {}
            for _, query_row in query_df.iterrows():
                qid = query_row['qid']
                query = query_row['query']

                # Perform the retrieval using PyTerrier
                ranks = br.search(query)
                ranks = ranks[:num_results]
                ranks['qid'] = qid
                ranks['query'] = ''
                ranks['text'] = ''
                ranks = ranks.sort_values(by=['score'], ascending=False)
                ranks.rename(columns={'score': 'ret_score'}, inplace=True)
                ranks.drop(columns={'docid', 'rank'}, inplace=True)

                # Fill in the query and text columns
                for index, row in ranks.iterrows():
                    ranks.at[index, 'query'] = query
                    docno = row['docno']
                    text_match = text_df[text_df['docno'] == docno]
                    if not text_match.empty:
                        text = text_match.iloc[0]['text']
                        ranks.at[index, 'text'] = text_match.iloc[0]['text']
                        ranks.at[index, 'fuzzy_score'] = calculate_fuzzy_matching_score(query, text)
                        ranks.at[index, 'smith_waterman_score'] = calculate_smith_waterman_similarity(query, text)
                        ranks.at[index, 'lcs_score'] = calculate_lcs(query, text)

                # Reset index and store in dictionary
                ranks.reset_index(drop=True, inplace=True)
                results_dict[qid] = ranks

            return results_dict

        # Call the search function and return the results
        return pyterrier_search_function(query_df, num_results=10, text_df=text_df)  # Example num_results