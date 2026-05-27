import argparse
import json
import re
import requests
import pandas as pd
from pathlib import Path
from typing import List, Dict, Optional, Any


def get_concept_links(
    query: str,
    top_k: int = 5,
    use_reranker: bool = False,
    url: str = "http://127.0.0.1:8000/api/conceptlink"
) -> List[Dict[str, Any]]:
    """
    Retrieve concept link candidates from the Concept Linking API.

    Args:
        query (str): The query string to search for.
        top_k (int, optional): Number of top candidates to retrieve. Defaults to 5.
        use_reranker (bool, optional): Whether to ask the API to use GPT-based reranking.
        url (str, optional): API endpoint URL.

    Returns:
        list[dict[str, Any]]: A list of concept link dictionaries.

    Raises:
        requests.exceptions.RequestException: If the request to the API fails.
    """
    payload = {
        "query": query, 
        "top_k": top_k, 
        "use_reranker": use_reranker
    }
    headers = {
        "accept": "application/json", 
        "content-type": "application/json"
    }

    response = requests.post(url, json=payload, headers=headers)
    response.raise_for_status()
    return response.json().get("conceptlinks", [])


def get_input_data(file_path: Optional[Path] = None, delimiter: str = ";") -> List[Dict[str, str]]:
    """
    Retrieve input data from a file or prompt for manual input.

    Args:
        file_path (Path, optional): Path to input file (CSV, Excel, or JSON).
            If None, manual input is prompted.
        delimiter (str, optional): Delimiter for CSV files. Defaults to ';'.

    Returns:
        list[dict[str, str]]: A list of dictionaries with keys 'Greek' and 'English'.
    """
    if file_path is None:
        greek = input("Enter Greek term (optional): ").strip()
        english = input("Enter English term (required): ").strip()
        return [{"Greek": greek, "English": english}]

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(file_path, delimiter=delimiter)
    elif suffix in (".xls", ".xlsx"):
        df = pd.read_excel(file_path)
    elif suffix == ".json":
        df = pd.read_json(file_path)
    else:
        raise ValueError("Unsupported file format. Use CSV, Excel, or JSON.")

    return df.to_dict(orient="records")


def _make_safe_filename(greek: Optional[str], english: str) -> Path:
    """Generate a Windows-safe filename from Greek or English terms.

    If the Greek term is provided and not empty, it is used as the base
    filename. Otherwise, the English term is used. The English term is
    guaranteed to be non-empty by the calling code.

    All forbidden characters for Windows file names are replaced with
    underscores, and consecutive underscores are collapsed into a single one.

    Args:
        greek (Optional[str]): Greek term (may be None or empty).
        english (str): English term (non-empty string).

    Returns:
        Path: A `Path` object pointing to the safe filename with a `.json` extension.
    """
    greek_str = (greek or "").strip()
    base = greek_str if greek_str else english.strip()

    # Remove forbidden characters for Windows filenames
    forbidden = r'[<>:"/\\|?*]'
    safe = re.sub(forbidden, "_", base)

    # Collapse multiple underscores
    safe = re.sub(r"_+", "_", safe)

    return Path(f"{safe}.json")


def main() -> None:
    """Main entry point for the concept linking tool."""
    parser = argparse.ArgumentParser(description="Concept linking tool with optional GPT reranking.")
    parser.add_argument(
        "-f", "--file",
        type=Path,
        help="Path to input file (CSV, Excel, or JSON). If not provided, manual input is used."
    )
    parser.add_argument(
        "-d", "--delimiter",
        type=str,
        default=";",
        help="CSV delimiter (default: ';')."
    )
    parser.add_argument(
        "-k", "--top-k",
        type=int,
        default=5,
        help="Number of top candidates to retrieve (default: 5)."
    )
    parser.add_argument(
        "--use-reranker",
        action="store_true",
        help="Use GPT-based reranking in the API."
    )
    parser.add_argument(
        "--greek-col",
        type=str,
        default="Greek",
        help="Name of the column containing Greek terms (optional; default: 'Greek')."
    )
    parser.add_argument(
        "--eng-col",
        type=str,
        default="English",
        help="Name of the column containing English terms (required; default: 'English')."
    )
    args = parser.parse_args()

    query_list = get_input_data(args.file, delimiter=args.delimiter)

    for i, row in enumerate(query_list, start=1):
        # Greek is optional
        greek_query = None
        if args.greek_col and args.greek_col in row:
            greek_val = row.get(args.greek_col)
            greek_query = "" if pd.isna(greek_val) else str(greek_val).strip()

        # English is required
        try:
            eng_val = row[args.eng_col]
        except KeyError:
            print(f"[Row {i}] Missing required English column '{args.eng_col}'. Skipping.")
            continue

        eng_query = "" if pd.isna(eng_val) else str(eng_val).strip()
        if not eng_query:
            print(f"[Row {i}] English term is empty. Skipping.")
            continue

        try:
            candidates = get_concept_links(
                eng_query,
                top_k=args.top_k,
                use_reranker=args.use_reranker
            )
            print(f"Retrieved {len(candidates)} candidates for '{eng_query}'.")

            if not candidates:
                print("No candidates found.")
                continue

            query_data = {
                "greek": greek_query or "",
                "english": eng_query,
                "conceptlinks": candidates
            }

            print(f"Output: {query_data}")

            safe_filename = _make_safe_filename(greek_query, eng_query)
            with safe_filename.open("w", encoding="utf-8") as f:
                json.dump(query_data, f, ensure_ascii=False, indent=4)

        except requests.exceptions.RequestException as e:
            print(f"[Row {i}] Request failed:", e)
        except Exception as e:
            print(f"[Row {i}] An error occurred:", e)


if __name__ == "__main__":
    main()