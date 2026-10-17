import json
import pkgutil


def load(name):
    """Load a JSON file from this world's data folder (works inside a zipped .apworld)."""
    return json.loads(pkgutil.get_data(__package__, f"data/{name}").decode("utf-8"))
