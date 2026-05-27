from pydantic import BaseModel, Field
from typing import List

class ConceptLinkRequest(BaseModel):
    # Input to the /conceptlink API
    query: str = Field(
        ...,
        title="Input query",
        description="Clinical term (str) to link to standard vocubulary",
        example = "Heart Attack"
    )

    top_k: int = Field(
        5, 
        description="Number of top links to return", 
        ge=1, 
        example=3
    )

    use_reranker: bool = Field(
        False,
        description="Whether to rerank candidates using the reasoning model.",
        example=False,
    )

class ConceptLink(BaseModel):
    # One individual concept link
    concept_name: str = Field(
        ..., 
        description="Linked concept name", 
        example="Chest pain"
    )

    concept_code: str = Field(
        ..., 
        description="Concept code", 
        example="29857009"
    )

    concept_id: int = Field(
        ..., 
        description="OMOP concept ID", 
        example=31967
    )

    domain: str = Field(
        ...,
        description="Domain of the concept", 
        example="Condition"
    )

    vocabulary_id: str = Field(
        ..., 
        description="Vocabulary source", 
        example="SNOMED"
    )

    score: float = Field(
        ...,
        description="Similarity score (0.0-1.0) from FAISS or normalized reranker confidence.",
        ge=0.0,
        le=1.0,
        example=0.92
    )
    
    justification: str = Field(
        ...,
        description="Explanation of why this concept was chosen, provided by the reranker if available.",
        example="Matches the core meaning of the query and aligns with expected clinical context."
    )

class ConceptLinkResponse(BaseModel):
    # The full list of concept links
    conceptlinks: List[ConceptLink] = Field(
        ...,
        description="List of linked concepts"
    )
