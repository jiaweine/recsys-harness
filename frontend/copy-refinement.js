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

function refineDynamicCopy(root = document) {
  root.querySelectorAll?.('.evidence-item a').forEach(link => {
    if (link.textContent !== '打开来源') link.textContent = '打开来源';
  });

  const taskTitle = document.getElementById('taskTitle');
  if (taskTitle?.textContent === '新的体验任务') taskTitle.textContent = '新任务';

  const taskState = document.getElementById('taskState');
  if (taskState?.textContent === '正在自主执行') taskState.textContent = '执行中';

  root.querySelectorAll?.('.timeline .empty').forEach(empty => {
    if (empty.textContent.includes('记录系统实际做过的每一步')) empty.textContent = '执行后显示实际步骤。';
  });
}

function applyInitialPrompt() {
  const welcome = document.getElementById('welcome');
  const taskTitle = document.getElementById('taskTitle');
  if (!welcome || welcome.hidden || !['新任务', '新的体验任务'].includes(taskTitle?.textContent || '')) return;
  refineDynamicCopy();
  const active = document.querySelector('.scene.active[data-scene]');
  setPrompt(active?.dataset.scene || 'search');
}

document.addEventListener('click', event => {
  const sceneButton = event.target.closest('.scene[data-scene]');
  if (sceneButton) {
    setTimeout(() => {
      refineDynamicCopy();
      setPrompt(sceneButton.dataset.scene);
    }, 0);
    return;
  }
  if (event.target.closest('#newTaskBtn')) {
    setTimeout(() => {
      refineDynamicCopy();
      const active = document.querySelector('.scene.active[data-scene]');
      setPrompt(active?.dataset.scene || 'search');
    }, 0);
  }
});

const copyObserver = new MutationObserver(records => {
  for (const record of records) {
    for (const node of record.addedNodes) {
      if (node.nodeType === Node.ELEMENT_NODE) refineDynamicCopy(node);
    }
  }
  refineDynamicCopy();
});
copyObserver.observe(document.body, { childList: true, subtree: true, characterData: true });
refineDynamicCopy();

if (document.body.classList.contains('ready')) {
  applyInitialPrompt();
} else {
  const readyObserver = new MutationObserver(() => {
    if (!document.body.classList.contains('ready')) return;
    readyObserver.disconnect();
    applyInitialPrompt();
  });
  readyObserver.observe(document.body, { attributes: true, attributeFilter: ['class'] });
}