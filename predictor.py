'''
Predictor.py
-----------------------
Input: Predictor Class returns the optimal wine that we predict for a user.
Predictor takes in a history of past wine recommendations as well as the
unsupervised learning algorithm that clustered the wines. The algorithm
then first hard clusters data (followed by soft-cluster depending on if
EM algorithm was used as the unsupervised learning algorithm). Afterwards,
the optimal wine is selected by maximizing a cost function.
'''
import numpy as np
import random
from scipy import sparse
from scipy.sparse import csr_matrix
from cluster_em import ClusterEM
from cluster import Cluster
from scipy.spatial.distance import cosine
from sklearn.cluster import KMeans

'''
History brought in as a File of JSON obects in the form of:

{
user_feedback: -1 | 1,
cluster: [0, k]
confidence: # always 1 if k-means, if EM prob returned
features: [...]
score: R
price: R
wine_id: MD5 hash
}

'''
'''
select_cluster: returns the index of the cluster in which we want to begin
our analysis of the ideal wine selection.s
'''

THRESHOLD = 0.2
LAMBDA = 100
ETA = 0.0000001
NUM_BETS = 3
NUM_WILDCARDS = 1

class Predictor(object):

    def __init__(self, examples, features):
        self.examples = examples
        self.features = features

    def select_cluster(self, history):
        history_wines = history.get_history()
        num_clusters = len(history_wines[0]['cluster_scores'])
        probs = np.zeros(num_clusters) #Final probabilities that we will return (multinomial)
        pos = np.zeros(num_clusters)
        neg = np.zeros(num_clusters)
        num_wines = np.zeros(num_clusters)
        denominator = 0
        for wine in history_wines:
            cluster_assignment = np.argmax(wine['cluster_scores'])
            user_feedback = wine['user_feedback']
            if(user_feedback == 1):
                pos[cluster_assignment] += 1
            else:
                neg[cluster_assignment] += 1
            num_wines[cluster_assignment] += 1

        pos_total = np.sum(pos)
        neg_total = np.sum(neg)
        neg_total = 1 if neg_total == 0 else neg_total
        num_total = np.sum(num_wines)

        probs = [num_wines[k] * (pos[k] / pos_total) * (1-neg[k] / neg_total) for k in range(num_clusters)]
        probs = probs / sum(probs)
        choice = np.random.choice(range(num_clusters), p=probs)
        # print('Selected cluster:',choice)
        return choice

    # Source: https://philbull.wordpress.com/2012/09/27/drawing-random-numbers-from-a-multivariate-gaussian/
    def multivariate_sample(self, mean, covariance):
        N = len(mean)
        # mean = ... # some array of length N
        # cov = ... # some positive-definite NxN matrix
        # draws = 1

        # # Do factorisation (can store this and use it again later)
        # L = np.linalg.cholesky(covariance)

        # # Get 3*draws Gaussian random variables (mean=0, variance=1)
        # norm = np.random.normal(size=draws*N).reshape(N, draws)

        # # Construct final set of random numbers (with correct mean)
        # rand = mean + np.matmul(L, norm)[0]

        return mean + [np.random.normal(loc=mean[i], scale=covariance) for i in range(len(mean))]

    def select_cluster_coordinates(self, history, cluster_index, covariance, model):
        cluster_history = []
        history_wines = history.get_history()
        for wine in history_wines: 
            if np.argmax(np.asarray(wine['cluster_scores'])) == cluster_index:
                cluster_history.append(wine)
        random_history_wine = random.choice(cluster_history)
        random_history_wine_index = random_history_wine['true_index']
        # sample_mean = self.features[random_history_wine_index].toarray()[0]
        sample_mean = model.em.means_[cluster_index]
        # print('Sampling from a multivariate normal...')
        benchmark_coordinates = sample_mean if covariance == 0 else self.multivariate_sample(sample_mean, covariance)
        # print('Selecting nearest wine from the following benchmark coordinate:')
        # print(benchmark_coordinates)
        return benchmark_coordinates

    def get_search_space(self, model, history, cluster_index, benchmark_coordinates):
        search_space = []
        if(type(model) == ClusterEM):
            em_model = model.em
            cluster_scores = em_model.predict_proba(benchmark_coordinates.reshape(1, -1))[0]
            # print(cluster_scores)
            cluster_scores = sorted([(cluster_scores[j], j) for j in range(len(cluster_scores))], key=lambda x: x[0], reverse=True)
            em_assignments = np.load('em_assignments.npy')
            target_clusters = set()
            target_clusters.add(cluster_scores[0][1])
            if(cluster_scores[0][0] - cluster_scores[1][0] < THRESHOLD):
                target_clusters.add(cluster_scores[1][1])
            # print('Selecting from the following clusters...', target_clusters)
            for i in range(len(em_assignments)):
                max_assignment = np.argmax(em_assignments[i,:])
                if max_assignment in target_clusters: 
                    search_space.append(i) # appending the ith index of the wine in dataset
        else:
            kmeans_model = model.kmeans
            for i in range(len(kmeans_model.assignments_)): 
                if kmeans_model.assignments_[i] == cluster_index: 
                    search_space.append(i)

        return search_space

    def select_wine(self, benchmark_coordinates, search_space, examples, features, current_recommendations):
        prices = []
        for example in examples:
            price_key = ''
            if 'price' in example.keys():
                price_key = 'price'
            else:
                price_key = 'price:'
            price_string = example[price_key]
            slash_index  = price_string.rfind('/')
            price_string = price_string[1:] if slash_index == -1 else price_string[1:slash_index]
            prices.append(int(price_string) if price_string.isdigit() else float('inf'))
        scores = []
        for example in examples:
            scores.append(float('-inf') if not example['score'].isdigit() else float(example['score']))
        quality = np.asarray([prices[true_index] / scores[true_index] for true_index in search_space])
        similarity = np.asarray([-1 * np.linalg.norm(features[true_index].toarray()[0] - benchmark_coordinates) for true_index in search_space])
        cost = np.multiply(ETA, quality) - np.multiply(LAMBDA, similarity)
        selection = np.argmin(cost)
        while selection in current_recommendations:
            cost[selection] = float('inf')
            selection = np.argmin(cost)
        # print('Choosing wine with index',selection,'with price', prices[selection])
        return selection

    def predictWine(self, seen, model, history, examples, features, isWildCard):
        cluster_index = self.select_cluster(history) # one cluster index
        covariance = 0
        if type(model) == ClusterEM:
            covariance = model.get_covariances()[cluster_index]
        if isWildCard:
            covariance *= 20
        benchmark_coordinates = self.select_cluster_coordinates(history, cluster_index, covariance, model) # Selecting the wine options from which we will optimize to find the ideal wine
        search_space = self.get_search_space(model, history, cluster_index, benchmark_coordinates)
        recommendation = self.select_wine(benchmark_coordinates, search_space, examples, features, seen)
        return recommendation

    def predict(self, model, history, examples, features, demoClusters=None):
        if demoClusters:
            return self.predictDemo(model, history, examples, features, demoClusters)

        recommendations = []
        for i in range(NUM_BETS):
            recommendations.append(self.predictWine(recommendations, model, history, examples, features, False))
        for i in range(NUM_WILDCARDS):
            recommendations.append(self.predictWine(recommendations, model, history, examples, features, True))
        return recommendations    

    def predictWineDemo(self, seen, model, history, examples, features, cluster_index):
        benchmark_coordinates = model.em.means_[cluster_index]
        search_space = self.get_search_space(model, history, cluster_index, benchmark_coordinates)
        recommendation = self.select_wine(benchmark_coordinates, search_space, examples, features, seen)
        return recommendation

    # only works for EM!!
    def predictDemo(self, model, history, examples, features, demoClusters):
        recommendations = []
        for i in range(NUM_BETS):
            cluster_index = random.choice(demoClusters)
            recommendations.append(self.predictWineDemo(recommendations, model, history, examples, features, cluster_index))
        for i in range(NUM_WILDCARDS):
            cluster_index = random.choice(range(len(model.em.means_)))
            recommendations.append(self.predictWineDemo(recommendations, model, history, examples, features, cluster_index))
        return recommendations

