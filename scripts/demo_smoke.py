from lingjing_harness.sample_data import build_sample_catalog
from lingjing_harness.runtime import AgentHarness

for prompt in [
    '最近搜索“露营灯”不准，帮我优化但先不要上线',
    '看看用户 u-lin 的推荐首屏，给我一个可验证的改进方案',
    '做一次全局体检',
]:
    result=AgentHarness(build_sample_catalog()).run(prompt)
    print('\n>',prompt)
    print(result['answer'])
