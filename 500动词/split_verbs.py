#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将500动词.md拆分成每个动词一个文件，并更新主文件为引用格式
"""

import re
import os
from pathlib import Path

def parse_verb_section(lines, start_idx):
    """解析一个动词部分，返回动词名、分类路径、例句列表和结束索引"""
    if start_idx >= len(lines):
        return None, None, [], start_idx
    
    # 查找动词名（单独一行，首字母大写，全小写或混合）
    verb_name = None
    category_path = []
    i = start_idx
    
    # 向前查找分类路径（中文标题）
    # 查找最近的分类标题，构建层级路径
    j = i - 1
    found_categories = []
    while j >= 0 and j >= start_idx - 50:  # 最多向前查找50行
        line = lines[j].strip()
        # 查找中文分类标题（不含数字、不含英文动词、不含特殊标记）
        if line and not re.match(r'^\d+\.', line) and not re.match(r'^[A-Z][a-z]+$', line) and not line.startswith('>') and not line.startswith('-') and not line.startswith('@') and not line.startswith('Table of Contents') and not line.startswith('Bibliography') and not line.startswith('学术英文') and not line.startswith('五百动词语境习词') and not line.startswith('例句库'):
            # 检查是否是中文（包含中文字符）
            if re.search(r'[\u4e00-\u9fff]', line):
                # 清理行（移除可能的标记）
                clean_line = re.sub(r'^[IVX]+\.\s*', '', line)  # 移除罗马数字
                clean_line = re.sub(r'^[A-H]\.\s*', '', clean_line)  # 移除字母标记
                clean_line = clean_line.strip()
                # 移除可能的页码（如"7"）
                clean_line = re.sub(r'\s+\d+$', '', clean_line)
                clean_line = clean_line.strip()
                if clean_line and clean_line not in found_categories:
                    found_categories.insert(0, clean_line)
        j -= 1
    
    # 构建分类路径（取最后3-4级）
    if found_categories:
        # 保留主要分类（I-VIII），但构建完整路径
        # 查找主要分类标记（I. 界定, II. 位置变化等）
        main_category = None
        sub_category = None
        detail_category = None
        
        for cat in found_categories:
            if cat in ['界定', '位置变化', '状态变化', '程度变化', '关系', '主观', '静态', '行为']:
                main_category = cat
            elif cat in ['定边界', '定概念', '定结构', '定内含', '位移', '离合', '秩序化', '聚合', '提取', '离散', '修正', '从有到无', '从无到有', '变大', '变小', '消极', '对立', '协调', '相关', '因果', '主次', '细化', '动机', '辩论', '认知', '评估', '取向']:
                sub_category = cat
            else:
                if not detail_category:
                    detail_category = cat
        
        # 构建路径
        if main_category:
            if sub_category:
                if detail_category:
                    category_path = [main_category, sub_category, detail_category]
                else:
                    category_path = [main_category, sub_category]
            else:
                category_path = [main_category]
        else:
            # 如果没有找到主要分类，使用找到的最后几个
            category_path = found_categories[-3:] if len(found_categories) >= 3 else found_categories
    
    # 查找动词名
    while i < len(lines):
        line = lines[i].strip()
        # 动词名：单独一行，首字母大写，后面是小写字母
        if re.match(r'^[A-Z][a-z]+$', line):
            verb_name = line
            i += 1
            break
        i += 1
    
    if not verb_name:
        return None, None, [], start_idx
    
    # 解析例句
    examples = []
    current_example = None
    
    while i < len(lines):
        line = lines[i].strip()
        
        # 如果遇到下一个动词或新的分类标题，停止
        if re.match(r'^[A-Z][a-z]+$', line) and line != verb_name:
            break
        if line and re.search(r'[\u4e00-\u9fff]', line) and not line.startswith('>') and not line.startswith('-') and not re.match(r'^\d+\.', line):
            # 可能是新的分类标题
            if i > start_idx + 5:  # 至少已经解析了一些内容
                break
        
        # 例句编号
        if re.match(r'^\d+\.', line):
            if current_example:
                examples.append(current_example)
            current_example = {
                'number': line.split('.')[0],
                'english': line,
                'chinese': '',
                'words': []
            }
        # 中文翻译
        elif line.startswith('>'):
            if current_example:
                current_example['chinese'] = line[1:].strip()
        # 单词解释
        elif line.startswith('-'):
            if current_example:
                current_example['words'].append(line[1:].strip())
        # 空行，可能是例句结束
        elif not line and current_example and current_example['english']:
            # 检查下一个非空行是否是新的例句
            next_non_empty = None
            for j in range(i + 1, min(i + 3, len(lines))):
                if lines[j].strip():
                    next_non_empty = lines[j].strip()
                    break
            if next_non_empty and (re.match(r'^\d+\.', next_non_empty) or re.match(r'^[A-Z][a-z]+$', next_non_empty)):
                examples.append(current_example)
                current_example = None
        
        i += 1
    
    # 添加最后一个例句
    if current_example:
        examples.append(current_example)
    
    return verb_name, category_path, examples, i

def create_verb_file(verb_name, category_path, examples, output_dir):
    """为动词创建单独的文件"""
    filename = f"{verb_name.lower()}.md"
    filepath = os.path.join(output_dir, filename)
    
    # 构建分类路径字符串
    if category_path:
        # 过滤掉太通用的标题
        filtered_path = [p for p in category_path if p not in ['学术英文', '五百动词语境习词', '例句库']]
        if filtered_path:
            category_str = '/'.join(filtered_path[-3:])  # 最多取最后3级
        else:
            category_str = '未分类'
    else:
        category_str = '未分类'
    
    # 写入文件
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('---\n')
        f.write('tags:\n')
        f.write(f'  - {category_str}\n')
        f.write('---\n')
        f.write('\n')
        f.write('  \n')
        f.write('\n')
        
        for example in examples:
            f.write(f"{example['number']}. {example['english'][len(example['number'])+1:].strip()}\n")
            f.write('\n')
            if example['chinese']:
                f.write(f"> {example['chinese']}\n")
                f.write('\n')
            for word in example['words']:
                f.write(f"- {word}\n")
                f.write('\n')
            f.write('  \n')
            f.write('\n')
    
    return filename, len(examples)

def update_main_file(input_file, output_file, verb_files, output_dir):
    """更新主文件，使用引用格式"""
    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    lines = content.split('\n')
    new_lines = []
    i = 0
    
    while i < len(lines):
        line = lines[i].strip()
        
        # 检查是否是动词名
        if re.match(r'^[A-Z][a-z]+$', line):
            verb_name = line
            verb_lower = verb_name.lower()
            
            # 查找对应的文件
            if verb_lower in verb_files:
                filename, example_count = verb_files[verb_lower]
                # 替换为引用格式
                new_lines.append(f"@{filename} (1-{example_count})")
                # 跳过原动词的所有例句，直到下一个动词或分类标题
                i += 1
                while i < len(lines):
                    next_line = lines[i].strip() if i < len(lines) else ''
                    # 遇到下一个动词或新的分类标题，停止
                    if re.match(r'^[A-Z][a-z]+$', next_line) and next_line != verb_name:
                        break
                    if next_line and re.search(r'[\u4e00-\u9fff]', next_line) and not next_line.startswith('>') and not next_line.startswith('-') and not re.match(r'^\d+\.', next_line):
                        if i > 0 and lines[i-1].strip() != verb_name:
                            break
                    i += 1
                continue
            else:
                # 如果没找到对应的文件，保留原内容
                new_lines.append(lines[i])
        else:
            new_lines.append(lines[i])
        
        i += 1
    
    # 写入新文件
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(new_lines))

def main():
    input_file = '500动词.md'
    output_dir = '.'
    
    if not os.path.exists(input_file):
        print(f"错误：找不到文件 {input_file}")
        return
    
    print(f"正在读取 {input_file}...")
    with open(input_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # 解析所有动词
    verb_files = {}
    i = 0
    verb_count = 0
    
    print("正在解析动词...")
    while i < len(lines):
        verb_name, category_path, examples, next_idx = parse_verb_section(lines, i)
        
        if verb_name and examples:
            verb_lower = verb_name.lower()
            filename, example_count = create_verb_file(verb_name, category_path, examples, output_dir)
            verb_files[verb_lower] = (filename, example_count)
            verb_count += 1
            print(f"  已处理: {verb_name} ({example_count}个例句) -> {filename}")
            i = next_idx
        else:
            i += 1
    
    print(f"\n共处理 {verb_count} 个动词")
    
    # 更新主文件
    print("\n正在更新主文件...")
    update_main_file(input_file, f"{input_file}.new", verb_files, output_dir)
    print(f"新主文件已保存为: {input_file}.new")
    print("\n完成！")

if __name__ == '__main__':
    main()

