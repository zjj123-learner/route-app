# main.py
# 程序入口: 命令行交互
# 运行: python main.py
# 输入多行任务, 空行结束, 打印最优行程

from parser import parse_tasks
from optimizer import optimize_route


def to_hhmm(minute):
    """把'一天中的第几分钟'变成 'HH:MM' 字符串"""
    h, m = divmod(minute, 60)
    return f"{h:02d}:{m:02d}"


def fmt_task_list(arrivals):
    """把排序结果变成可读的多行文本"""
    lines = []
    for i, s in enumerate(arrivals):
        task = s["task"]
        place = task["place"] or "位置待定"
        priority = {1: "低", 2: "中", 3: "高"}[task["priority"]]
        lines.append(
            f"{i+1}. {task['name']}"
            f"\n    地点: {place}  优先级: {priority}"
            f"\n    到达: {to_hhmm(s['arrival'])}  停留: {task['duration']}分钟"
        )
    return "\n".join(lines)


def read_tasks():
    """从命令行读任务, 每行一个, 空行结束"""
    print("请输入任务(每行一个, 直接回车结束):")
    lines = []
    while True:
        line = input()
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines)


def main():
    print("=== 智能行程规划 V1 (命令行版) ===")
    start = {"name": "家", "lat": 31.235, "lng": 121.47}

    while True:
        text = read_tasks()
        if not text.strip():
            print("没有输入任务, 再见")
            break
        tasks = parse_tasks(text)
        if not tasks:
            print("没有解析出任务, 请检查输入")
            continue
        try:
            result = optimize_route(tasks, start)
        except ValueError as e:
            print("错误:", e)
            continue
        print("\n推荐顺序:")
        print(fmt_task_list(result["arrivals"]))
        stats = result["stats"]
        print(f"\n总路程: {stats['travel']}分钟, 总等待: {stats['wait']}分钟, 成本: {stats['total']}")
        again = input("\n继续?(y/n): ").strip().lower()
        if again != "y":
            print("再见")
            break


if __name__ == "__main__":
    main()