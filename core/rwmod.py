import hashlib
import os

from core.translate import analysis_style, is_text_key_valid, translate_inifile
from utlis.ini_lib import IniFile, found_ini_files


class RWMod:
    def __init__(self, path: str) -> None:
        self.unit_datas: list[IniFile] = []
        self.files_count: int = 0
        self.path = path
        self.uuid = hashlib.md5(self.path.encode("utf-8")).hexdigest()
        self.style = ""

        if os.path.isfile(path) and path.endswith(".rwmod"):
            # TODO 解压
            pass

        if os.path.isdir(path):
            print(f"[{self.uuid}] 扫描ini、mod-info.txt和template文件")

            for file_path in found_ini_files(path):
                try:
                    self.unit_datas.append(IniFile(file_path))
                except Exception as e:
                    print(e)

            self.files_count = len(self.unit_datas)

        print(f"[{self.uuid}] 扫描完成")

    async def analysis_style(self, use_cache: bool = True) -> str:
        print(f"[{self.uuid}] 分析风格信息")
        text_keys: list[tuple[IniFile, str, str]] = []
        max_case_length = 30
        max_file_length = 10
        max_text_length = 500
        for inifile in self.unit_datas[:max_file_length]:
            for section in inifile.data.keys():
                for key in inifile.data[section]:
                    if len(text_keys) >= max_case_length:
                        break
                    if is_text_key_valid(key):
                        text_keys.append((inifile, section, key))

        style_analysis_case = []

        # 提取部分当作风格案例
        for inifile, section, key in text_keys[:max_case_length]:
            style_analysis_case.append(inifile.data[section][key])
        style_analysis_case_text = "\n----------\n".join(style_analysis_case)[
            :max_text_length
        ]

        style = analysis_style(style_analysis_case_text)
        return style

    async def translate_all(self):
        for inifile in self.unit_datas:
            await translate_inifile(inifile, translate_style=self.style)
