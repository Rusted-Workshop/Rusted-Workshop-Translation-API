import os
from utlis.ini_lib import found_ini_files, IniFile


class RWMod:
    def __init__(self, path: str) -> None:
        self.unit_datas: list[IniFile] = []
        self.files_count: int = 0

        if os.path.isfile(path) and path.endswith(".rwmod"):
            # TODO 解压
            pass

        if os.path.isdir(path):
            for file_path in found_ini_files(path):
                try:
                    self.unit_datas.append(IniFile(file_path))
                except Exception as e:
                    print(e)

            self.files_count = len(self.unit_datas)

        mod_info_filepath = os.path.join(path, "mod-info.txt")
        if "mod-info.txt" in os.listdir(path):
            mod_info = IniFile(mod_info_filepath)
            self.unit_datas.append(mod_info)
