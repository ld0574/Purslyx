<template>
  <div id="product-home">
    <header class="topbar"><RouterLink class="brand-button" to="/"><img class="brand-mark" src="/favicon.svg" alt="" aria-hidden="true" />Purslyx</RouterLink><span class="topbar-spacer" /><RouterLink class="button soft" to="/app/login">登录</RouterLink><RouterLink class="button primary" to="/app/register">创建账号</RouterLink></header>
    <main class="public-main">
      <section class="hero">
        <div class="hero-copy"><div class="eyebrow">AI CAREER COMPANION</div><h1>从真实经历，<span>走到下一步。</span></h1><p>围绕确认过的资料完成岗位匹配、简历表达和面试准备。每个重要结论都能回到原始经历与冻结版本。</p><div class="hero-actions"><RouterLink class="button primary" to="/app/register">免费开始</RouterLink><RouterLink class="button outline" to="/app/login">登录工作台</RouterLink></div><div class="proof-row"><div><strong>证据优先</strong>不凭空补写经历</div><div><strong>条件分离</strong>能力与求职期望分开</div><div><strong>双端协作</strong>求职与招聘统一规则</div></div></div>
        <div class="hero-visual"><div class="visual-window"><div class="window-top"><strong>岗位匹配报告</strong><span class="tag success">可复核</span></div><div class="window-lines"><i class="window-line blue" /><i class="window-line" /><i class="window-line short" /><i class="window-line blue" /></div><div class="visual-score"><strong>86</strong><small>能力分</small></div></div></div>
      </section>
      <section class="product-section"><div class="section-heading"><div><div class="eyebrow">FOR BOTH SIDES</div><h2>不同角色，各有清晰路径</h2></div><p>求职、招聘与授权管理围绕同一套证据和条件规则协作，同时保持数据与身份边界。</p></div><div class="capability-grid"><article class="capability-card"><span class="capability-index">01</span><h3>求职工作台</h3><p>资料与多条期望、匹配池、证据报告、事实改写、岗位版 PDF 和完整面试。</p><RouterLink to="/app/register">以求职者身份开始 →</RouterLink></article><article class="capability-card"><span class="capability-index">02</span><h3>招聘工作台</h3><p>JD、候选人资料、候选人明确期望、单人分析、待核实问题与成本统计。</p><RouterLink to="/app/register">以招聘方身份开始 →</RouterLink></article><article class="capability-card"><span class="capability-index">03</span><h3>授权管理</h3><p>用户、角色权限、次数、站点概况、反馈和三类脱敏日志，所有写操作留审计。</p><RouterLink to="/app/login">管理员登录 →</RouterLink></article></div></section>
    </main>
  </div>
</template>
