import re


def extract_data_from_filenames(filenames):
    pattern = r"LDC_SMD_(\d+-\d+)_E2E_PCQ\.pdf"
    extracted_data = []
    for filename in filenames:
        match = re.search(pattern, filename)
        if match:
            extracted_data.append(match.group(1))
    return extracted_data


def print_chunked_data(data, chunk_size=10):
    for i in range(0, len(data), chunk_size):
        print(", ".join(data[i:i + chunk_size]))


def main():
    input_data = input("Enter the list of filenames (comma separated): ")
    filenames = [filename.strip() for filename in input_data.split(",")]
    extracted_list = extract_data_from_filenames(filenames)
    print("\nExtracted data:")
    print_chunked_data(extracted_list)
