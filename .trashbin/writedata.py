import os

def SequentialWrite(data, file_path):
    # 파일이 존재하지 않으면 파일을 만듭니다.
    if not os.path.exists(file_path):
        with open(file_path, 'w', encoding='utf-8') as file:
            file.write(data + '\n')
        return
    
    # 파일의 기존 내용을 읽어옵니다.
    with open(file_path, 'r', encoding='utf-8') as file:
        lines = file.readlines()
    
    # 새 줄을 첫 줄에 추가합니다.
    lines.insert(0, data + '\n')
    
    # 모든 내용을 파일에 다시 씁니다.
    with open(file_path, 'w', encoding='utf-8') as file:
        file.writelines(lines)
