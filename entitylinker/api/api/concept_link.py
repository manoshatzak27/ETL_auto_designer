from fastapi import APIRouter, Depends, Query
from api.dependencies import get_linker, get_reranker
from entitylinker.linker.concept_linker import ConceptLinker
from entitylinker.linker.reranker import Reranker
from api.schema import ConceptLinkRequest, ConceptLink, ConceptLinkResponse

router = APIRouter()

@router.post("/conceptlink", response_model=ConceptLinkResponse)
def link_term(
    req: ConceptLinkRequest,
    linker: ConceptLinker = Depends(get_linker),
    reranker: Reranker = Depends(get_reranker),

):
    # Step 1: Get FAISS top_k candidates
    raw_results = linker.find_best_concept(req.query, req.top_k)

    # Step 2: Convert FAISS tuples → schema if reranker not used
    if not req.use_reranker:
        conceptlinks = [
            ConceptLink(
                concept_name=name,
                concept_code=code,
                concept_id=concept_id,
                domain=domain,
                vocabulary_id=vocab,
                score=score,
                justification="Cosine similarity score"
            )
            for name, code, concept_id, domain, vocab, score in raw_results
        ]
        return ConceptLinkResponse(conceptlinks=conceptlinks)

    # Step 3: Rerank using GPT reasoning
    reranked_results = reranker.rerank(req.query, raw_results)

    # Step 4: Convert GPT JSON → schema
    conceptlinks = [
        ConceptLink(
            concept_name=item["concept_name"],
            concept_code=item["concept_code"],
            concept_id=int(item["concept_id"]),
            domain=item["domain_id"],
            vocabulary_id=item["vocab"],
            score=(item.get("confidence_score", 0) / 10.0),  # Normalize 1–10 to 0–1
            justification=item['justification']
        )
        for item in reranked_results
    ]

    return ConceptLinkResponse(conceptlinks=conceptlinks)
