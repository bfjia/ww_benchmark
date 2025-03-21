#!/usr/bin/env python3

import sys
import os
import shutil
import argparse
import pandas as pd
import glob
import datetime as dt
import subprocess
import lzma
import io

def main():
    parser = argparse.ArgumentParser(description="Preprocess reference collection: randomly select samples and write into individual files in lineage-specific directories.")
    parser.add_argument('-m, --metadata', dest='metadata', type=str, required=False, default="../../reqfiles/gisaidmetadata.tsv", help="metadata tsv file for full sequence database")
    parser.add_argument('-f, --fasta', dest='fasta_in', type=str, required=False, default = "../../reqfiles/gisaidseq.fasta.xz", help="fasta file representing full sequence database")
    # parser.add_argument('-i, --index', dest='fasta_index', type=str, required=True, help="fasta index file")
    parser.add_argument('-k', dest='select_k', type=int, default=1000, help="randomly select 1000 sequences per lineage")
    parser.add_argument('--max_N_content', type=float, default=0.001, help="remove genomes with N rate exceeding this threshold")
    parser.add_argument('--min_seq_len', type=int, default=25000, help="remove genomes shorter than this threshold")
    parser.add_argument('--min_seqs_per_lin', type=int, default=1, help="skip lineages with fewer sequences in the input fasta")
    parser.add_argument('--continent', dest='continent', type=str, help="only consider sequences found in specified continent")
    parser.add_argument('--country', dest='country', type=str, default = "USA", help="only consider sequences found in specified country")
    parser.add_argument('--state', dest='state', type=str, help="only consider sequences found in specified state")
    parser.add_argument('--startdate', dest='startdate', type=dt.date.fromisoformat, help="only consider sequences found on or after this date; input should be ISO format")
    parser.add_argument('--enddate', dest='enddate', type=dt.date.fromisoformat, default="2022-12-01", help="only consider sequences found on or before this date; input should be ISO format")
    parser.add_argument('--seed', dest='seed', default=0, type=int, help="random seed for sequence selection")
    parser.add_argument('-o, --outdir', dest='outdir', type=str, default="seqs_per_lineage",help="output directory")
    parser.add_argument('--verbose', action='store_true')
    # parser.add_argument('--test_sed', action='store_true')
    args = parser.parse_args()

    # create output directory
    if os.path.exists(args.outdir):
        print("WARNING: overwriting output directory {}".format(args.outdir))
        shutil.rmtree(args.outdir)
    os.makedirs(args.outdir)

    # read metadata
    metadata_df = read_metadata(args.metadata,
                                args.max_N_content,
                                args.min_seq_len)
    # remove duplicate sequences
    metadata_df.drop_duplicates(subset=["Virus name",
                                        "Collection date",
                                        "Submission date"],
                                inplace=True,
                                ignore_index=True)
    # extract lineage info
    lineages = metadata_df["Pango lineage"].unique()
    lineage_counts = metadata_df["Pango lineage"].value_counts()

    # select sequences
    selection_dict = {}
    lineages_with_sequence = []
    for lin_id in lineages:
        # skip unclassified sequences
        if lin_id == "Unassigned":
            continue
        # skip lineages with too few sequences
        if lineage_counts[lin_id] < args.min_seqs_per_lin:
            continue

        # filter for lineage, country and length
        samples = metadata_df.loc[metadata_df["Pango lineage"] == lin_id]
        # add extra row to avoid pandas bug (https://github.com/pandas-dev/pandas/issues/35807)
        samples = samples.append(pd.Series({"Location" : ". / . / ."}),
                                 ignore_index=True)
        samples[["continent", "country", "state"]] = \
            samples["Location"].str.split(" / ", n=2, expand=True)
        if args.continent:
            samples = samples.loc[samples["continent"] == args.continent]
        else:
            samples = samples.loc[samples["continent"] != "."]
        if args.country:
            samples = samples.loc[samples["country"] == args.country]
        else:
            samples = samples.loc[samples["country"] != "."]
        if args.state:
            samples = samples.loc[samples["state"] == args.state]
        if args.startdate:
            samples = samples.loc[
                        samples["date"] >= pd.to_datetime(args.startdate)]
        if args.enddate:
            samples = samples.loc[
                        samples["date"] <= pd.to_datetime(args.enddate)]
        # randomly select sequences
        select_n = min(len(samples), args.select_k)
        selection = samples.sample(n=select_n, random_state=args.seed)
        if select_n == 0:
            if args.verbose:
                print("WARNING: no sequences satisfying country restrictions for lineage {}".format(lin_id))
            continue
        elif select_n == 1:
            gisaid_id = selection["Accession ID"].item()
            seq_name = selection["Virus name"].item()
            # seq_name = "{}|{}|{}".format(selection["Virus name"].item(),
            #                              selection["Collection date"].item(),
            #                              selection["Submission date"].item())
            # print(seq_name)
            selection_dict[seq_name] = (lin_id, gisaid_id)
        else:
            gisaid_ids = list(selection["Accession ID"])
            seq_names = list(selection["Virus name"])
            collection_dates = list(selection["Collection date"])
            submission_dates = list(selection["Submission date"])
            for i, gisaid_id in enumerate(gisaid_ids):
                seq_name = seq_names[i]
                # seq_name = '{}|{}|{}'.format(seq_names[i],
                #                              collection_dates[i],
                #                              submission_dates[i])
                # print(seq_name)
                selection_dict[seq_name] = (lin_id, gisaid_id)
        lineages_with_sequence.append(lin_id)
        # create lineage directory
        try:
            os.mkdir("{}/{}".format(args.outdir, lin_id))
        except FileExistsError:
            print("ERROR: directory for {} already exists".format(lin_id))
            sys.exit(1)

    print("{} sequences selected".format(len(selection_dict.keys())))
    # write sequences to separate files
    print("searching fasta and writing sequences to output directory...")
    args.test_sed = False
    args.fasta_index = ""
    if args.test_sed:
        fasta_index = read_index(args.fasta_index)
        for seq_id, info in selection_dict.items():
            (lin_id, gisaid_id) = info
            outfile = "{}/{}/{}.fa".format(args.outdir, lin_id, gisaid_id)
            [start_idx, end_idx] = fasta_index[seq_id]
            subprocess.check_call("sed -n '{},{}p;{}q' {} > {}".format(
                    start_idx, end_idx, end_idx+1, args.fasta_in, outfile),
                    shell=True)
    else:
        #with open(args.fasta_in, 'r') as f_in:
        with lzma.open(args.fasta_in, 'rb') as f_in:
            buffer = io.BufferedReader(f_in)
            for _ in range(11):
                next(buffer)
            firstline = ">hCoV-19/USA/CA-HLX-STM-XUQ4AYHZG/2022|2022-12-27|2023-01-27"
            firstlinechanged = False
            keep_line = False
            line_idx = 0
            selection_idx = 0
            for line in buffer:
                try:
                    line = line.decode().strip()
                except Exception as e:
                    if not firstlinechanged:
                        line = firstline
                        firstlinechanged = True
                    else:
                        sys.exit(1)

                if line[0] == '>':
                    # new record starts here
                    # first store previous record
                    if keep_line:
                        outfile = "{}/{}/{}.fa".format(args.outdir, lin_id, gisaid_id)
                        with open(outfile, 'w') as f_out:
                            f_out.write(">{}\n{}\n".format(seq_id, seq))
                    # now parse new record identifier
                    line_idx += 1
                    if args.verbose and line_idx % 100000 == 0:
                        print("{} sequences from input fasta processed".format(line_idx))
                        print("{} sequences from selection found".format(selection_idx))
                    seq_id = line.rstrip('\n').lstrip('>').split('|')[0]
                    # seq_id = line.rstrip('\n').lstrip('>').split()[0]
                    try:
                        lin_id, gisaid_id = selection_dict[seq_id]
                        seq = ""
                        keep_line = True
                        selection_idx += 1
                        # now remove key from dict to avoid writing duplicates
                        del selection_dict[seq_id]
                    except KeyError as e:
                        # item not found as sequence was not selected
                        keep_line = False
                elif keep_line:
                    # append nucleotide sequence
                    seq += line.rstrip('\n')
            # add final record if necessary
            if keep_line:
                outfile = "{}/{}/{}.fa".format(args.outdir, lin_id, gisaid_id)
                with open(outfile, 'w') as f_out:
                    f_out.write(">{}\n{}\n".format(seq_id, seq))
            print("{} sequences from input fasta processed".format(line_idx))
            print("{} sequences from selection found".format(selection_idx))

    # write lineages
    with open("{}/lineages.txt".format(args.outdir), 'w') as f:
        for lin_id in sorted(lineages_with_sequence):
            f.write("{} {}\n".format(lin_id, lineage_counts[lin_id]))

    return


def read_metadata(metadata_file, max_N_content, min_seq_len):
    """Read metadata from tsv into dataframe and filter for completeness"""
    df = pd.read_csv(metadata_file, sep='\t', header=0, dtype=str, skiprows=11)
    df = df.rename(columns={'Unnamed: 0': 'Virus name'})

    # adjust date representation in dataframe
    df["date"] = df["Collection date"].str.replace('-XX','-01')
    df["date"] = pd.to_datetime(df.date, yearfirst=True)
    # remove samples wich have no pangolin lineage assigned (NaN or None)
    df = df.loc[df["Pango lineage"].notna()]
    df = df.loc[df["Pango lineage"] != "None"]
    # remove samples which are marked as incomplete or N-content > threshold
    df = df.astype({"Is complete?" : 'bool',
                    "N-Content" : 'float',
                    "Sequence length" : 'int'})
    df["N-Content"] = df["N-Content"].fillna(0)
    df = df.loc[
            (df["Is complete?"] == True) & \
            (df["N-Content"] <= max_N_content) & \
            (df["Sequence length"] >= min_seq_len)]
    return df


def read_index(index_tsv):
    """Read fasta sequence index from file"""
    print("Reading fasta sequence index from file {}".format(index_tsv))
    index = {}
    with open(index_tsv, 'r') as f:
        for line in f:
            [seq_id, start_idx, end_idx] = line.rstrip('\n').split('\t')
            seq_id = seq_id.split('|')[0]
            index[seq_id] = [int(start_idx), int(end_idx)]
    return index


if __name__ == "__main__":
    sys.exit(main())
