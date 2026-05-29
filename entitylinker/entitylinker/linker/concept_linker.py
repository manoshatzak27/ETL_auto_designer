"""
Module for linking free-text medical terms to standard clinical concepts
from selected OMOP vocabularies (SNOMED, LOINC, …).
The core steps are:
1. Load and filter the OMOP `CONCEPT.csv` file for the chosen vocabularies
   and *standard* concepts only.
2. Encode each concept name into a dense vector using a SapBERT model
   fine-tuned on biomedical text.
3. Build a FAISS index (inner-product / cosine similarity) over the
   normalized embeddings.
4. Query the index at runtime to obtain the `top_k` most similar concepts
   for any user string.

Caching is automatic: on the first run the script computes embeddings and saves
three artefacts into the data directory (or user-supplied paths):
- `embeddings_<VOCABS>.npy`   - concept vectors
- `lookup_<VOCABS>.csv`       - filtered concept table
- `faiss_<VOCABS>.index`      - FAISS index
Subsequent runs load these caches instead of recomputing.
"""

import os
import faiss
import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from tqdm import tqdm
from pathlib import Path
from typing import List, Tuple, Union
from transformers import AutoTokenizer, AutoModel
from entitylinker.config import load_settings

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

class ConceptLinker:
    def __init__(
        self,
        *,
        config_path: Union[str, Path, None] = None,
        vocabularies: List[str] | None = None,
        concept_path: Union[str, Path] | None = None,
        embedding_file: Union[str, Path] | None = None,
        lookup_file: Union[str, Path] | None = None,
        index_file: Union[str, Path] | None = None,
        model_name: str = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext",
        export_files: bool = True,
        batch_size: int = 64,
        device: str | None = None,
    ) -> None:
        """
        Initialize the ConceptLinker.

        Args:
            config_path: Path to YAML config (required unless ENTITYLINKER_CONFIG env var is set).
            vocabularies: Optional override for vocabulary IDs (e.g., ["SNOMED", "LOINC"]).
            concept_path: Optional path to OMOP CONCEPT.csv (TSV). If None, uses <data_dir>/CONCEPT.csv from config.
            embedding_file: Optional path to cached .npy with embeddings.
            lookup_file: Optional path to cached .csv with filtered concepts.
            index_file: Optional path to cached FAISS index.
            model_name: Hugging Face model name for SapBERT.
            export_files: If False, do not save caches to disk.
            batch_size: Batch size for embedding.
            device: "cuda", "cpu", or None (auto).
        """
        self.export_files = export_files
        self.batch_size = batch_size

        # Load settings from YAML
        self._settings = load_settings(config_path=config_path)

        # Resolve vocabularies
        self.vocabularies: List[str] = vocabularies or self._settings.vocabularies
        if not self.vocabularies:
            raise ValueError(
                "No vocabularies provided. Set them in the YAML config, "
                "or pass vocabularies=[...]."
            )
        vocab_suffix: str = "_" + "_".join(self.vocabularies)

        # Resolve paths
        if concept_path is None:
            base_dir = self._settings.data_dir
            self.concept_path = base_dir / "CONCEPT.csv"  # OMOP file is tab-delimited, commonly named .csv
        else:
            self.concept_path = Path(concept_path)
            base_dir = self.concept_path.parent

        # Cache artefacts (allow overrides)
        self.embedding_file = Path(embedding_file) if embedding_file else base_dir / f"embeddings{vocab_suffix}.npy"
        self.lookup_file = Path(lookup_file) if lookup_file else base_dir / f"lookup{vocab_suffix}.csv"
        self.index_file = Path(index_file) if index_file else base_dir / f"faiss{vocab_suffix}.index"

        # Device
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)

        # Initialize model & data 
        self._initialize_model(model_name)
        self._load_or_create_embeddings()

    # Internal helpers
    def _initialize_model(self, model_name: str) -> None:
        """Load the Hugging Face tokenizer and model.

        Args:
            model_name (str): Name or path of the SapBERT model.
        """
        print("Loading tokenizer and model…")
        self.tokenizer: AutoTokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model: AutoModel = AutoModel.from_pretrained(model_name)
        self.model.eval().to(self.device)

    def _load_concepts(self, concept_path: Path) -> None:
        """Load and filter the OMOP concept table (expects TSV content)."""
        if not concept_path.exists():
            raise FileNotFoundError(f"CONCEPT file not found at: {concept_path}")

        print("Loading vocabulary concepts…")
        # OMOP tables are typically tab-delimited; keep dtype=str to avoid coercion
        concepts: pd.DataFrame = pd.read_csv(concept_path, dtype=str, delimiter="\t")

        # Filter to only selected vocabularies
        filtered_concepts: pd.DataFrame = concepts[concepts["vocabulary_id"].isin(self.vocabularies)].copy()

        # Keep only standard concepts and drop rows with missing names
        self.lookup: pd.DataFrame = (
            filtered_concepts[filtered_concepts["standard_concept"] == "S"]
            .dropna(subset=["concept_name"])
            .reset_index(drop=True)
        )
        if self.lookup.empty:
            raise ValueError(
                f"No concepts left after filtering for vocabularies {self.vocabularies} with standard_concept='S'. "
                "Check your YAML config and the CONCEPT file."
            )

    def _load_or_create_embeddings(self) -> None:
        """Load embeddings/FAISS index from disk or compute and cache them."""
        if self.embedding_file.exists() and self.lookup_file.exists() and self.index_file.exists():
            print("Loading cached embeddings and FAISS index…")
            self.embeddings: np.ndarray = np.load(self.embedding_file)
            self.lookup: pd.DataFrame = pd.read_csv(self.lookup_file)
            self.index: faiss.IndexFlatIP = faiss.read_index(str(self.index_file))
            return

        # If cache files do not exist, process from scratch
        self._load_concepts(self.concept_path)
        print("Embedding terms (this may take a few minutes on first run)…")

        concept_names = self.lookup["concept_name"].tolist()
        self.embeddings = self._embed_texts(concept_names, batch_size=self.batch_size)  # already L2-normalized

        print("Building FAISS index…")
        self.index = faiss.IndexFlatIP(self.embeddings.shape[1])  # Inner product = cosine on normalized vectors
        self.index.add(self.embeddings)

        if self.export_files:
            self.embedding_file.parent.mkdir(parents=True, exist_ok=True)
            np.save(self.embedding_file, self.embeddings)
            self.lookup.to_csv(self.lookup_file, index=False)
            faiss.write_index(self.index, str(self.index_file))

    def _embed_texts(self, texts: List[str], batch_size: int = 64) -> np.ndarray:
        """Embeds a list of text strings using the SapBERT model.

        Args:
            texts (List[str]): List of text strings to embed.
            batch_size (int, optional): Batch size for model inference.

        Returns:
            np.ndarray: Matrix of normalized text embeddings.
        """
        all_embeddings: list[np.ndarray] = []
        for i in tqdm(range(0, len(texts), batch_size), desc="Embedding texts"):
            batch: List[str] = texts[i :i + batch_size]
            # Tokenize inputs
            inputs = self.tokenizer(
                batch,
                return_tensors="pt",
                truncation=True,
                padding=True,
                max_length=25,
            )

            # Inference without gradients
            with torch.no_grad():
                outputs = self.model(**inputs)
                emb = outputs.last_hidden_state[:, 0, :]    # Use [CLS] token embedding
                emb = F.normalize(emb, p=2, dim=1)          # Normalize to unit length
                all_embeddings.append(emb.cpu().numpy())

        return np.vstack(all_embeddings)

    def find_best_concept(self, query: str, top_k: int = 5) -> List[Tuple[str, str, int, str, str, float]]:
        """Find the most semantically similar vocabulary concepts to a query.

        Args:
            query (str): Input query string.
            top_k (int, optional): Number of top results to return.

        Returns:
            List of tuples: (concept_name, concept_code, concept_id, domain_id, vocabulary_id, similarity)
        """
        # Embed the input query (already normalized by _embed_texts)
        query_emb: np.ndarray = self._embed_texts([query])[0]

        # FAISS similarity search
        distances, indices = self.index.search(np.expand_dims(query_emb, axis=0), top_k)

        # Map search results back to concept table
        results: List[Tuple[str, str, int, str, str, float]] = []
        for dist, idx in zip(distances[0], indices[0]):
            row = self.lookup.iloc[int(idx)]
            results.append(
                (
                    row["concept_name"],         # Term text
                    str(row["concept_code"]),    # Code
                    int(row["concept_id"]),      # OMOP concept ID
                    row["domain_id"],            # Domain
                    row["vocabulary_id"],        # SNOMED/LOINC etc.
                    round(float(dist), 5),       # Similarity score
                )
            )
        return results