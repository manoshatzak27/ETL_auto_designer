import os
import json
import logging
from typing import List, Tuple, Optional, Dict
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Change to DEBUG for more detailed logs

handler = logging.StreamHandler()
formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

class Reranker:
    """
    A class that uses OpenAI's reasoning model to rerank a list of candidate concepts.
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o") -> None:
        """
        Initialize the reranker with the OpenAI API key and model name.

        Args:
            api_key (str, optional): Your OpenAI API key. If None, reads from OPENAI_API_KEY env variable.
            model (str): The OpenAI model to use (default is "gpt-4o").
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key not provided and not found in environment.")
        
        self.model = model
        self.client = OpenAI(api_key=self.api_key)
        logger.info(f"Initialized Reranker with model '{self.model}'.")

    def rerank(
        self,
        query: str,
        candidates: List[Tuple[str, str, int, str, str, float]]
    ) -> List[Dict]:
        """
        Rerank a list of concept candidates based on reasoning.

        Args:
            query (str): The original clinical query.
            candidates (List): List of (concept_name, concept_code, concept_id, domain_id, vocabulary_id, similarity score).

        Returns:
            Tuple[str, str, str, float]: The selected (concept_name, concept_code, concept_id, vocab_id, sim_score).
        """
        logger.info("Starting rerank process.")
        logger.debug(f"Query: {query}")
        logger.debug(f"Top candidates: {[c for c in candidates]}")

        prompt = self._build_prompt(query, candidates)
        logger.info(f"Prompt sent to OpenAI:\n{prompt}")

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a clinical terminology assistant."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2
            )

            answer = response.choices[0].message.content.strip()
            logger.info(f"Model response: {answer}")
            if answer.startswith("```json"):
                answer = answer.strip("```json").strip("```").strip()

            answer = json.loads(answer)

        except Exception as e:
            logger.error(f"Error during reranking: {e}", exc_info=True)
            logger.warning("Falling back to top FAISS result.")
        
        return answer

    def _build_prompt(
        self,
        query: str,
        candidates: List[Tuple[str, str, int, str, str, float]]
    ) -> str:
        """
        Build the input prompt for OpenAI reasoning.

        Args:
            query (str): Free-text user input.
            candidates (List): Top-k candidate concept tuples.

        Returns:
            str: The formatted prompt.
        """
        candidate_block = "\n".join([
            f"{i+1}. {name} (code: {code}, ID: {cid}, domain: {domain}, vocab: {vocab})"
            for i, (name, code, cid, domain, vocab, score) in enumerate(candidates)
        ])

        prompt = f"""
            You are a clinical reasoning assistant that links short free-text clinical queries to standardized medical concepts.

            ### Input Query
            "{query}"

            ### Top Candidate Concepts (retrieved via semantic similarity):

            {candidate_block}

            ### Evaluation Criteria:
            1. **Core Meaning**: Do the query and candidate refer to the same medical concept?
            2. **Context**: Does the candidate belong in the expected clinical domain?
            3. **Linguistic Similarity**: Are the terms similar in language and structure?

            ### Additional Rules:
            - Avoid picking a candidate that is **more specific than the query**.
            - If unsure, favor a **less specific but broadly correct** term.
            - Ignore irrelevant formatting or naming conventions.

            ### Instructions:
            Evaluate **each candidate individually** and return a list of descending-ranked JSON objects.
            Each object should include:
            - `concept_name` (string): Candidate's name.
            - `concept_code` (string): Candidate Code.
            - `concept_id` (string): Candidate ID.
            - `domain_id` (string): Candidate Domain ID.
            - `vocab` (string): Vocabulary of ID.
            - `confidence_score` (integer, 1-10): Your confidence in the match.
            - `justification` (string): A short explanation of your reasoning, including whether the category matched.

            ### Output Format:
            Return only a **JSON array** of objects like this:

            ```json
            [
            {{
                "concept_name": "...",
                "concept_code": "...",
                "concept_id": "...",
                "domain_id": "...",
                "vocab": "...",
                "confidence_score": 7,
                "justification": "..."
            }},
            ...
            ]
            ```
        """

        return prompt
