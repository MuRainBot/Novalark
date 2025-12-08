"""
工具
"""
import functools
import inspect
from typing import Callable, Any, TypeVar, ParamSpec

T = TypeVar("T")
P = ParamSpec("P")


def pre_inject(**kwargs: P.kwargs):
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(func)
        def wrapper(*args_: P.args, **kwargs_: P.kwargs):
            kwargs_.update(kwargs)
            return func(args_, **kwargs_)

        return wrapper

    return decorator


class Depends_:
    def __init__(self, dependency: Callable[..., T]):
        self._dependency = dependency

    def get(self) -> T:
        return self._dependency()


def Depends(dependency: Callable[..., T]) -> T:
    return Depends_(dependency)


def inject_dependencies(func: Callable, dependencies: dict) -> dict[str, Any]:
    """
    一个简单的依赖注入函数。

    它会检查`func`的签名，并从`dependencies`字典中找到匹配的参数进行注入，
    然后执行函数并返回其结果。

    :param func: 需要被注入依赖并执行的目标函数。
    :param dependencies: 一个包含可用依赖项的字典，键是依赖项的名称。
    :return: dict: 包含注入的依赖项的参数字典。
    """
    # 1. 获取函数的签名
    sig = inspect.signature(func)

    # 2. 准备一个字典，用于存放需要传递给函数的参数
    kwargs_to_pass = {}

    # 3. 遍历函数签名的所有参数
    for param_name in sig.parameters:
        # 4. 如果参数名存在于我们的依赖字典中
        if param_name in dependencies:
            # 5. 将对应的键值对添加到准备传递的参数字典中
            kwargs_to_pass[param_name] = dependencies[param_name]
        elif isinstance(sig.parameters[param_name].default, Depends_):
            kwargs_to_pass[param_name] = sig.parameters[param_name].default.get()

    return kwargs_to_pass


if __name__ == "__main__":
    def get_db() -> str:
        return "db" + '123'

    # get_doc_db = pre_inject(db_name="doc")(get_db)

    a = Depends(get_db)

    def func(a: int = Depends(get_db), abc: int = 1):
        return a, abc

    print(func(**inject_dependencies(func, {"abc": 2, "d": 5})))
