"""Gemini embeddings for imported article passages, personal notes, and references."""
import math
import os

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

MODEL = os.environ.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-2")
DIMENSIONS = 768
CHUNK_CHARS = 5000
CHUNK_OVERLAP = 350


def get_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in .env.")
    return genai.Client(api_key=api_key)


def embed_document(title: str, text: str) -> list[float]:
    """Embed one user-provided passage, personal note, or reference summary."""
    contents = f"title: {title or 'none'} | text: {text}"
    client = get_client()
    response = client.models.embed_content(
        model=MODEL,
        contents=contents,
        config=types.EmbedContentConfig(output_dimensionality=DIMENSIONS),
    )
    if not response.embeddings or not response.embeddings[0].values:
        raise ValueError("Gemini returned an empty embedding.")
    return list(response.embeddings[0].values)


def embed_query(question: str) -> list[float]:
    contents = f"task: question answering | query: {question}"
    client = get_client()
    response = client.models.embed_content(
        model=MODEL,
        contents=contents,
        config=types.EmbedContentConfig(output_dimensionality=DIMENSIONS),
    )
    if not response.embeddings or not response.embeddings[0].values:
        raise ValueError("Gemini returned an empty query embedding.")
    return list(response.embeddings[0].values)


def split_article(text: str) -> list[tuple[int, int, str]]:
    """Split article text into overlapping chunks, keeping original offsets."""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_CHARS, len(text))
        if end < len(text):
            boundary = text.rfind("\n\n", start + CHUNK_CHARS // 2, end)
            if boundary > start:
                end = boundary
        if end <= start:
            end = min(start + CHUNK_CHARS, len(text))
        chunks.append((start, end, text[start:end]))
        if end >= len(text):
            break
        start = max(start + 1, end - CHUNK_OVERLAP)
    return chunks


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)
