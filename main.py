from core.rwmod import RWMod
from asyncio import run


async def main():
    rwmod = RWMod(
        path=r"C:\Program Files (x86)\Steam\steamapps\workshop\content\647960\2869088515"
    )
    # rwmod.style = await rwmod.analysis_style()
    rwmod.style = "简洁功能性"

    await rwmod.translate_all()


if __name__ == "__main__":
    run(main())
