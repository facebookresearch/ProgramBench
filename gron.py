import sys
import json

def json_to_gron(data, path='json'):
    if isinstance(data, dict):
        print(f"{path} = {{}};")
        for key, value in data.items():
            json_to_gron(value, f"{path}.{key}")
    elif isinstance(data, list):
        print(f"{path} = [];" )
        for index, value in enumerate(data):
            json_to_gron(value, f"{path}[{index}]")
    else:
        print(f"{path} = {json.dumps(data)};")

def main():
    if len(sys.argv) > 2:
        print("Usage: gron [file.json]", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) == 2:
        try:
            with open(sys.argv[1], 'r') as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"Error reading JSON: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            data = json.load(sys.stdin)
        except json.JSONDecodeError as e:
            print(f"Error reading JSON: {e}", file=sys.stderr)
            sys.exit(1)

    json_to_gron(data)

if __name__ == "__main__":
    main()