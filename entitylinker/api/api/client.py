import requests

class EntityLinkerClient:
    def __init__(self, base_url='http://127.0.0.1:8000/api'):
        self.base_url = base_url

    def conceptlink(self, query: str, top_k: int = 5):
        endpoint = f"{self.base_url}/conceptlink"
        params = {
            'query': query,
            'top_k': top_k
        }
        headers = {
            'accept': 'application/json'
        }

        response = requests.post(endpoint, params=params, headers=headers)
        response.raise_for_status()
        return response.json()

# Example usage
if __name__ == "__main__":
    client = EntityLinkerClient()
    result = client.conceptlink("Heart Attack", top_k=5)
    print(result)
