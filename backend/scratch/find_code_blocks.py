with open('../src/main.tsx', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

for idx, line in enumerate(lines):
    if 'function FormattedAnswer' in line:
        print(f"FormattedAnswer definition starts at line {idx+1}")
        # print 50 lines around it
        for j in range(max(0, idx - 5), min(len(lines), idx + 60)):
            print(f"{j+1}: {lines[j]}", end='')
        print("="*40)
