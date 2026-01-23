import numpy as np
import pandas as pd
import argparse
import sys

# Main simulation function
def simulate_counts(n_genes, n_ctrl, n_trt, de_prop, fold_change, dispersion, seed, output, gene_list=None):
    # Set the random seed for reproducibility
    np.random.seed(seed)

    # -----------------------------
    # Gene name generation
    # -----------------------------
    if gene_list:
        # Use real gene names from file if provided
        if len(gene_list) < n_genes:
            raise ValueError(f"Gene list contains only {len(gene_list)} names, fewer than n_genes={n_genes}.")
        genes = gene_list[:n_genes]
    else:
        # Otherwise generate dummy gene names
        genes = [f"gene_{i+1}" for i in range(n_genes)]

    # -----------------------------
    # Sample names
    # -----------------------------
    ctrl_samples = [f"ctrl_{i+1}" for i in range(n_ctrl)]
    trt_samples = [f"trt_{i+1}" for i in range(n_trt)]

    # -----------------------------
    # Select DE genes
    # -----------------------------
    n_de = int(n_genes * de_prop)                      # Number of differentially expressed genes
    de_indices = np.random.choice(n_genes, n_de, replace=False)
    non_de_indices = list(set(range(n_genes)) - set(de_indices))

    # -----------------------------
    # Set baseline expression levels (mean counts)
    # -----------------------------
    mu_ctrl = np.random.uniform(50, 2000, size=n_genes)  # realistic baseline counts
    mu_trt = mu_ctrl.copy()

    # Apply fold change to DE genes (50% upregulated, 50% downregulated)
    half = n_de // 2
    mu_trt[de_indices[:half]] *= fold_change
    mu_trt[de_indices[half:]] /= fold_change

    # -----------------------------
    # Simulate RNA-seq counts using Negative Binomial distribution
    # -----------------------------
    size = 1.0 / dispersion  # size parameter for NB
    counts = np.zeros((n_genes, n_ctrl + n_trt), dtype=int)

    for i in range(n_genes):
        p_ctrl = size / (size + mu_ctrl[i])    # NB probability for control
        p_trt = size / (size + mu_trt[i])      # NB probability for treatment

        # Simulate control and treated counts for gene i
        counts[i, :n_ctrl] = np.random.negative_binomial(size, p_ctrl, size=n_ctrl)
        counts[i, n_ctrl:] = np.random.negative_binomial(size, p_trt, size=n_trt)

    # -----------------------------
    # Create and save output table
    # -----------------------------
    df = pd.DataFrame(counts, index=genes, columns=ctrl_samples + trt_samples)
    df.to_csv(output, sep="\t")
    print(f"Simulation complete. File saved to: {output}")

# -----------------------------
# Command-line interface
# -----------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulate RNA-seq count data with DE genes and group variance")

    parser.add_argument("--n_genes", type=int, default=18000, help="Total number of genes")
    parser.add_argument("--n_ctrl", type=int, default=5, help="Number of control samples")
    parser.add_argument("--n_trt", type=int, default=5, help="Number of treated samples")
    parser.add_argument("--de_prop", type=float, default=0.1, help="Proportion of DE genes")
    parser.add_argument("--fold_change", type=float, default=4.0, help="Fold change for DE genes")
    parser.add_argument("--dispersion", type=float, default=0.3, help="Dispersion parameter for negative binomial")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--output", type=str, default="simulated_counts.tsv", help="Output TSV file")
    parser.add_argument("--genes_file", type=str, help="Optional path to file containing real gene names")

    args = parser.parse_args()

    # Load gene list if provided
    gene_list = None
    if args.genes_file:
        try:
            with open(args.genes_file) as f:
                gene_list = [line.strip() for line in f if line.strip()]
        except Exception as e:
            print(f"Error reading gene file: {e}", file=sys.stderr)
            sys.exit(1)

    # Run the simulation
    simulate_counts(
        n_genes=args.n_genes,
        n_ctrl=args.n_ctrl,
        n_trt=args.n_trt,
        de_prop=args.de_prop,
        fold_change=args.fold_change,
        dispersion=args.dispersion,
        seed=args.seed,
        output=args.output,
        gene_list=gene_list
    )