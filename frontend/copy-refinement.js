const prompts = {
  search: '检查搜索露营灯的结果，先复现相关性问题，再定位原因并验证一个改进方向，不改变当前策略。',
  recommend: '检查用户 u-lin 的推荐首屏，重点看重复、新鲜度和冷启动风险，并给出优先级。',
  evolve: '检查当前搜索和推荐体验，判断哪一侧更值得优化；证据足够时探索候选策略，不改变当前策略。',
  audit: '做一次全局体检，找出搜索和推荐里最值得先处理的三个问题。',
};

function setPrompt(scene) {
  const input = document.getElementById('input');
  if (!input || !prompts[scene]) return;
  input.value = prompts[scene];
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function applyInitialPrompt() {
  const welcome = document.getElementById('welcome');
  const taskTitle = document.getElementById('taskTitle');
  if (!welcome || welcome.hidden || taskTitle?.textContent !== '新的体验任务') return;
  const active = document.querySelector('.scene.active[data-scene]');
  setPrompt(active?.dataset.scene || 'search');
}

document.addEventListener('click', event => {
  const sceneButton = event.target.closest('.scene[data-scene]');
  if (sceneButton) {
    setTimeout(() => setPrompt(sceneButton.dataset.scene), 0);
    return;
  }
  if (event.target.closest('#newTaskBtn')) {
    setTimeout(() => {
      const active = document.querySelector('.scene.active[data-scene]');
      setPrompt(active?.dataset.scene || 'search');
    }, 0);
  }
});

if (document.body.classList.contains('ready')) {
  applyInitialPrompt();
} else {
  const observer = new MutationObserver(() => {
    if (!document.body.classList.contains('ready')) return;
    observer.disconnect();
    applyInitialPrompt();
  });
  observer.observe(document.body, { attributes: true, attributeFilter: ['class'] });
}
