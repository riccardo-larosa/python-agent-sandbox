import requests

api_url = "http://localhost:8002/execute/python/chart"

# Inside test_api.py
python_code = """
import matplotlib.pyplot as plt
print("--- Script starting ---")
plt.plot([1, 2, 3, 4])
print("--- Plot created ---")
# No title, labels, etc. Just the basic plot.
"""
# ... rest of your test_api.py script ...
payload = {"code": python_code}

try:
    response = requests.post(api_url, json=payload, timeout=70) # Slightly longer timeout

    if response.status_code == 200:
        # Check content type
        if 'image/png' in response.headers.get('content-type', ''):
            with open("test_output.png", "wb") as f:
                f.write(response.content)
            print("Successfully received chart and saved as test_output.png")
        else:
            print(f"Error: Expected PNG image, but got content type: {response.headers.get('content-type')}")
            print(f"Response text: {response.text}")

    else:
        print(f"Error: Request failed with status code {response.status_code}")
        try:
            error_data = response.json()
            print(f"Error detail: {error_data.get('detail', 'No detail provided')}")
        except requests.exceptions.JSONDecodeError:
            print(f"Error response (non-JSON): {response.text}")

except requests.exceptions.RequestException as e:
    print(f"Request Exception: {e}")
