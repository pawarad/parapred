import numpy as np
import pickle
from sklearn.model_selection import KFold
from data_provider import load_chains
from structure_processor import save_chain, save_structure, \
    produce_annotated_ab_structure, extended_epitope
from patchdock_tools import output_patchdock_ab_constraint, \
    output_patchdock_ag_constraint, process_transformations
from keras.callbacks import LearningRateScheduler, EarlyStopping
from sklearn.metrics import confusion_matrix, roc_auc_score, matthews_corrcoef

AB_STRUCT_SAVE_PATH = "data/{0}/{1}_AB.pdb"
AG_STRUCT_SAVE_PATH = "data/{0}/{1}_AG.pdb"
AB_PATCHDOCK_SAVE_PATH = "data/{0}/{1}_ab_patchdock.txt"
AG_PATCHDOCK_SAVE_PATH = "data/{0}/{1}_ag_patchdock.txt"
PATCHDOCK_RESULTS_PATH = "data/{0}/{1}.txt"

#
# def annotate_and_save_test_structures(summary_file, probs, folder="annotated"):
#     chains = load_chains(summary_file)
#     for i, (ag_chain, ab_h_chain, ab_l_chain, pdb_name) in enumerate(chains):
#         p = probs[6*i:6*(i+1), :]
#         ab_struct = produce_annotated_ab_structure(ab_h_chain, ab_l_chain, p)
#         save_structure(ab_struct, AB_STRUCT_SAVE_PATH.format(folder, pdb_name))
#
#         save_chain(ag_chain, AG_STRUCT_SAVE_PATH.format(folder, pdb_name))
#
#         abpd_fname = AB_PATCHDOCK_SAVE_PATH.format(folder, pdb_name)
#         output_patchdock_ab_constraint(ab_struct, filename=abpd_fname)
#
#         agpd_fname = AG_PATCHDOCK_SAVE_PATH.format(folder, pdb_name)
#         ext_epi = extended_epitope(ag_chain, ab_h_chain, ab_l_chain, cutoff=5.0)
#         output_patchdock_ag_constraint(ext_epi, ag_chain.id, filename=agpd_fname)
#
#
# def capri_evaluate_test_structures(summary_file, folder="results"):
#     num_decoys = {'high': 0, 'med': 0, 'low': 0}
#
#     chains = load_chains(summary_file)
#     for ag_chain, ab_h_chain, ab_l_chain, pdb_name in chains:
#         trans_file = PATCHDOCK_RESULTS_PATH.format(folder, pdb_name)
#         q = process_transformations(trans_file, ag_chain, ab_h_chain, ab_l_chain)
#         if q is not None: num_decoys[q] += 1
#
#     return num_decoys


def kfold_cv_eval(model_func, dataset, output_file="crossval-data.p",
                  weights_template="weights-fold-{}.h5", seed=0):
    cdrs, lbls, masks = dataset["cdrs"], dataset["lbls"], dataset["masks"]
    kf = KFold(n_splits=10, random_state=seed, shuffle=True)

    all_lbls = []
    all_probs = []
    all_masks = []

    for i, (train_idx, test_idx) in enumerate(kf.split(cdrs)):
        print("Fold: ", i + 1)

        cdrs_train, lbls_train, mask_train = \
            cdrs[train_idx], lbls[train_idx], masks[train_idx]
        cdrs_test, lbls_test, mask_test = \
            cdrs[test_idx], lbls[test_idx], masks[test_idx]

        example_weight = np.squeeze((lbls_train * 1.5 + 1) * mask_train)
        # test_ex_weight = np.squeeze((lbls_test * 1.5 + 1) * mask_test)
        model = model_func()

        rate_schedule = lambda e: 0.001 if e >= 5 else 0.01

        model.fit([cdrs_train, np.squeeze(mask_train)],
                  lbls_train, batch_size=32, epochs=16,
                  # For informational purposes about the best number of epochs
                  # TODO: replace for proper evaluation
                  # validation_data=([cdrs_test, np.squeeze(mask_test)],
                  #                  lbls_test, test_ex_weight),
                  sample_weight=example_weight,
                  callbacks=[LearningRateScheduler(rate_schedule)])

        model.save_weights(weights_template.format(i))

        probs_test = model.predict([cdrs_test, np.squeeze(mask_test)])
        all_lbls.append(lbls_test)
        all_probs.append(probs_test)
        all_masks.append(mask_test)

    lbl_mat = np.concatenate(all_lbls)
    prob_mat = np.concatenate(all_probs)
    mask_mat = np.concatenate(all_masks)

    with open(output_file, "wb") as f:
        pickle.dump((lbl_mat, prob_mat, mask_mat), f)


def flatten_with_lengths(matrix, lengths):
    seqs = []
    for i, example in enumerate(matrix):
        seq = example[:lengths[i]]
        seqs.append(seq)
    return np.concatenate(seqs)


def compute_classifier_metrics(labels, probs, threshold=0.5):
    matrices = []
    aucs = []
    mcorrs = []

    for l, p in zip(labels, probs):
        aucs.append(roc_auc_score(l, p))
        l_pred = (p > threshold).astype(int)
        matrices.append(confusion_matrix(l, l_pred))
        mcorrs.append(matthews_corrcoef(l, l_pred))

    matrices = np.stack(matrices)
    mean_conf = np.mean(matrices, axis=0)
    errs_conf = 2 * np.std(matrices, axis=0)

    tps = matrices[:, 1, 1]
    fns = matrices[:, 1, 0]
    fps = matrices[:, 0, 1]

    recalls = tps / (tps + fns)
    precisions = tps / (tps + fps)

    rec = np.mean(recalls)
    rec_err = 2 * np.std(recalls)
    prec = np.mean(precisions)
    prec_err = 2 * np.std(precisions)

    fscores = 2 * precisions * recalls / (precisions + recalls)
    fsc = np.mean(fscores)
    fsc_err = 2 * np.std(fscores)

    auc_scores = np.array(aucs)
    auc = np.mean(auc_scores)
    auc_err = 2 * np.std(auc_scores)

    mcorr_scores = np.array(mcorrs)
    mcorr = np.mean(mcorr_scores)
    mcorr_err = 2 * np.std(mcorr_scores)

    print("Mean confusion matrix and error")
    print(mean_conf)
    print(errs_conf)

    print("Recall = {} +/- {}".format(rec, rec_err))
    print("Precision = {} +/- {}".format(prec, prec_err))
    print("F-score = {} +/- {}".format(fsc, fsc_err))
    print("ROC AUC = {} +/- {}".format(auc, auc_err))
    print("MCC = {} +/- {}".format(mcorr, mcorr_err))


def open_crossval_results(folder="cv-ab-seq", num_results=10, loop_filter=None):
    class_probabilities = []
    labels = []

    for r in range(num_results):
        result_filename = "{}/run-{}.p".format(folder, r)
        with open(result_filename, "rb") as f:
            lbl_mat, prob_mat, mask_mat = pickle.load(f)

        # Get entries corresponding to the given loop
        if loop_filter is not None:
            lbl_mat = lbl_mat[loop_filter::6]
            prob_mat = prob_mat[loop_filter::6]
            mask_mat = mask_mat[loop_filter::6]

        # Discard sequence padding
        seq_lens = np.sum(np.squeeze(mask_mat), axis=1)
        p = flatten_with_lengths(prob_mat, seq_lens)
        l = flatten_with_lengths(lbl_mat, seq_lens)

        class_probabilities.append(p)
        labels.append(l)

    return labels, class_probabilities
