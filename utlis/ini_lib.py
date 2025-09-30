import configparser
import os
import re
import sys
from typing import Any

import chardet
from pydantic import BaseModel, Field, create_model


def generate_model_from_dict(
    data: dict[str, Any], model_name: str = "Model"
) -> type[BaseModel]:
    """
    从简单字典 (dict[str, str]) 生成 Pydantic 模型。
    """
    top_fields = {}
    for key, value in data.items():
        if isinstance(value, str):
            top_fields[key] = (str, Field(..., description=value))
        else:
            top_fields[key] = (str, Field(..., description=str(value)))
    try:
        return create_model(
            model_name,
            __base__=BaseModel,
            __module__=sys._getframe(1).f_globals["__name__"],
            **top_fields,
        )
    except Exception as e:
        print(f"Error creating model with fields: {top_fields}")
        print(f"Data sample: {dict(list(data.items())[:2])}")
        raise e


class IniFile:
    def __init__(self, ini_file_path: str) -> None:
        self.path: str = ini_file_path
        try:
            self.data: dict[str, dict[str, str]] = read_ini_file(
                ini_file_path=self.path
            )
        except configparser.ParsingError:
            # 尝试自动修复
            self.data = read_ini_file(content=auto_fix(self.path))

        for section in self.data.keys():
            for key in self.data[section]:
                value = self.data[section][key]
                self.data[section][key] = value.replace("\\n", "\n")


def read_file(file_path) -> str:
    """
    自动检测文件编码并读取内容
    """
    try:
        # 1. 读取文件的原始字节数据
        with open(file_path, "rb") as file:
            raw_data = file.read()

        # 2. 检测编码
        encoding_result = chardet.detect(raw_data)
        detected_encoding = encoding_result["encoding"]

        if detected_encoding is None:
            raise ValueError("Detected encoding is None.")

        # 3. 使用检测到的编码读取文本
        text_content = raw_data.decode(detected_encoding)

        return text_content

    except UnicodeDecodeError as e:
        print(f"解码错误: {e}")
        # 尝试使用 fallback 编码
        fallback_encodings = ["utf-8", "gbk", "gb2312", "latin-1"]
        for enc in fallback_encodings:
            try:
                text_content = raw_data.decode(enc)
                return text_content
            except UnicodeDecodeError:
                continue
        raise e


def auto_fix(ini_file_path: str) -> str:
    """
    自动修复常见格式错误
    :param ini_file_path: INI文件路径
    :return: 修复后的内容
    """
    # 验证目录是否存在
    if not os.path.exists(ini_file_path):
        raise FileNotFoundError(f"The file {ini_file_path} does not exist.")

    # 验证路径是否为文件
    if not os.path.isfile(ini_file_path):
        raise NotADirectoryError(f"{ini_file_path} is a directory, not a file.")

    content = read_file(ini_file_path)

    # 修复1: k v 空格分隔的格式错误 -> 转换为 key: value 格式
    pattern1 = re.compile(
        r"(?P<key>\w+(?:\s+\w+)*)\s+(?P<value>[\d.]+)", flags=re.MULTILINE
    )

    def repl_kv(m: re.Match) -> str:
        k = m.group("key").strip()
        v = m.group("value").strip()
        return f"{k}: {v}"

    content = re.sub(pattern1, repl_kv, content)

    # 修复2: 删除未闭合的节
    lines = []
    for line in content.splitlines():
        if line.startswith("#") or line.startswith(";"):
            continue
        if line.strip() in ["#", ""]:
            lines.append(line)
            continue
        elif "[" in line and "]" in line:
            lines.append(line)
            continue
        elif "[" not in line and "]" not in line and ":" in line:
            lines.append(line)

    content = "\n".join(lines)

    # 格式化: 清理多余的空行和空格
    content = re.sub(r"\n\s*\n\s*\n", "\n\n", content)  # 清理多余空行
    content = re.sub(r"^\s+", "", content, flags=re.MULTILINE)  # 清理行首空格
    content = content.replace("%", "\\%")

    return content


def found_ini_files(dir_path: str) -> list[str]:
    """
    查找目录下所有的INI文件

    :param dir_path: 目录路径
    :return: INI文件路径列表
    """

    # 验证目录是否存在
    if not os.path.exists(dir_path):
        raise FileNotFoundError(f"The directory {dir_path} does not exist.")

    # 验证路径是否为目录
    if not os.path.isdir(dir_path):
        raise NotADirectoryError(f"The path {dir_path} is not a directory.")

    # 遍历目录，查找所有的INI文件
    ini_files_paths = []
    for root, dirs, files in os.walk(dir_path):
        for file in files:
            if file.endswith(".ini"):
                ini_files_paths.append(os.path.join(root, file))

    # 格式化路径，替换反斜杠为正斜杠
    ini_files_paths = [path.replace("\\", "/") for path in ini_files_paths]

    return ini_files_paths


def read_ini_file(
    ini_file_path: str | None = None, content: str | None = None
) -> dict[str, dict[str, str]]:
    """
    读取INI文件内容

    :param content: ini 内容
    :param ini_file_path: INI文件路径
    :return: INI文件内容字典
    """
    if not ini_file_path and not content:
        raise Exception("Argument can't not be empty")

    # 验证文件是否存在
    if ini_file_path and not os.path.exists(ini_file_path):
        raise FileNotFoundError(f"The file {ini_file_path} does not exist.")

    # 验证路径是否为文件
    if ini_file_path and not os.path.isfile(ini_file_path):
        raise FileNotFoundError(f"The path {ini_file_path} is not a file.")

    # 读取INI
    config = configparser.RawConfigParser(strict=False)

    if ini_file_path:
        content = read_file(ini_file_path)
        config.read_string(content)
    elif content:
        config.read_string(content)

    # 将INI文件内容转换为字典
    ini_content: dict = {
        section: dict(config[section]) for section in config.sections()
    }

    return ini_content
