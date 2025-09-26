from core.rwmod import RWMod
from core.translate import translate_inifile
from concurrent.futures import ThreadPoolExecutor

rwmod = RWMod(
    path=r"C:\Program Files (x86)\Steam\steamapps\workshop\content\647960\2869088515"
)

with ThreadPoolExecutor(max_workers=10) as executor:
    for unit_data in rwmod.unit_datas:
        executor.submit(lambda: print(translate_inifile(unit_data).data))
