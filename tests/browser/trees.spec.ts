import { test, expect } from '@playwright/test'
import { metadataForest, organizationGroups } from '../../src/trees'

test('both organization pages separate branches sharing an office and filter their members',async({page})=>{
  const boot=await (await page.request.get('/api/bootstrap')).json()
  if(boot.setup_required)await page.request.post('/api/setup',{data:{username:'admin',name:'林知远',password:'Browser-test-2026',demo:true}})
  await page.request.post('/api/auth/login',{data:{username:'admin',password:'Browser-test-2026'}})
  const branches=[{office:'O1056',code:'TY',name:'太原'},{office:'O1056',code:'CD',name:'成都'},{office:'O1056',code:'HZ',name:'杭州'}]
  const departments=[{id:'ty',office:'O1056',branch_code:'TY',branch_name:'太原',code:'D1',name:'张晓玲团队',active:1},{id:'cd',office:'O1056',branch_code:'CD',branch_name:'成都',code:'D2',name:'业务部',active:1}]
  const members=[
    {id:'ty-active',office:'O1056',branch_code:'TY',branch_name:'太原',department_code:'D1',department:'张晓玲团队',name:'张晓玲',employee_id:'TY001',status:'active',accounts:[]},
    {id:'ty-inactive',office:'O1056',branch_code:'TY',branch_name:'太原',department_code:'D1',department:'张晓玲团队',name:'张晓玲',employee_id:'TY002',status:'inactive',accounts:[]},
    {id:'cd-inactive',office:'O1056',branch_code:'CD',branch_name:'成都',department_code:'D2',department:'业务部',name:'张晓玲',employee_id:'CD001',status:'inactive',accounts:[]}
  ]
  await page.route('**/api/schema',async route=>{
    const data=await (await route.fetch()).json()
    data.schemas.lawyers.branches=branches
    data.schemas.lawyers.fields.find((f:any)=>f.key==='department').suggestions=departments
    await route.fulfill({json:data})
  })
  await page.route(/\/api\/(records\/lawyers|admin\/members)(\?|$)/,route=>route.fulfill({json:{items:members,total:members.length}}))
  await page.route(/\/api\/admin\/departments(\?|$)/,route=>route.fulfill({json:{items:departments,total:departments.length}}))
  await page.goto('/#lawyers')
  for(const mode of ['team','admin']){
    if(mode==='admin'){
      await page.getByRole('button',{name:'管理后台',exact:true}).click()
      await page.getByRole('button',{name:'部门与成员',exact:true}).click()
      await expect(page.getByRole('button',{name:'成员列表',exact:true})).toHaveCount(0)
    }
    const sidebar=page.getByLabel('部门树',{exact:true}),list=page.locator('.lawyer-team-members')
    await expect(sidebar.locator('.team-tree-office')).toHaveCount(3)
    await sidebar.locator('.team-tree-office .team-tree-node').filter({hasText:'太原'}).click()
    await expect(list.locator('tbody tr')).toHaveCount(2)
    await expect(list).toContainText('TY001')
    await expect(list).not.toContainText('CD001')
    await page.getByLabel('筛选团队状态').selectOption('active')
    await expect(list.locator('tbody tr')).toHaveCount(1)
    await page.getByLabel('筛选团队状态').selectOption('')
    await sidebar.getByRole('button',{name:/张晓玲团队/}).click()
    await expect(list.locator('tbody tr')).toHaveCount(2)
    if(mode==='admin')await expect(list.getByRole('button',{name:'编辑部门',exact:true})).toBeVisible()
    await sidebar.locator('.team-tree-office .team-tree-node').filter({hasText:'成都'}).click()
    await expect(list.locator('tbody tr')).toHaveCount(1)
    await expect(list).toContainText('CD001')
    await sidebar.locator('.team-tree-office .team-tree-node').filter({hasText:'杭州'}).click()
    await expect(list.getByText('当前机构或部门暂无匹配成员')).toBeVisible()
    await sidebar.locator('.team-tree-office .team-tree-node').filter({hasText:'太原'}).click()
    await page.screenshot({path:`artifacts/screenshots/branches-${mode}.png`,fullPage:true})
  }
})

test('tree preserves orphans, breaks cycles and separates same-name departments',()=>{
  const input=[{code:'C',parent_code:'B'},{code:'B',parent_code:'A'},{code:'A',parent_code:'0'},{code:'D',parent_code:'missing'},{code:'X',parent_code:'Y'},{code:'Y',parent_code:'X'},{code:'SELF',parent_code:'SELF'}].map(x=>({...x,id:x.code,name:x.code}))
  const tree=metadataForest(input)
  expect(tree.find(x=>x.code==='A')?.children[0].children[0].code).toBe('C')
  expect(tree.map(x=>x.code)).toEqual(['A','D','X','Y','SELF'])
  const departments=[{office:'SH',code:'D1',name:'工程部'},{office:'SH',code:'D2',name:'工程部'},{office:'BJ',code:'D1',name:'工程部'}]
  const members=[{id:'1',office:'SH',department_code:'D1',department:'工程部'},{id:'2',office:'SH',department_code:'D2',department:'工程部'},{id:'3',office:'BJ',department_code:'D1',department:'工程部'},{id:'4',office:'SH',department:''}]
  const groups=organizationGroups(members,departments)
  expect(groups).toHaveLength(2)
  expect(groups[0].children).toHaveLength(3)
  expect(groups[0].children[0].members.map((x:any)=>x.id)).toEqual(['1'])
  const branchGroups=organizationGroups([
    {id:'active',office:'O1056',branch_code:'TY',branch_name:'太原',department_code:'D1',department:'业务部'},
    {id:'historical',office:'O1056',branch_code:'CD',branch_name:'成都',department_code:'D2',department:'业务部'},
    {id:'unassigned',office:'O1056',branch_code:'TY',branch_name:'太原'}
  ],[
    {office:'O1056',branch_code:'TY',branch_name:'太原',code:'D1',name:'业务部'},
    {office:'O1056',branch_code:'CD',branch_name:'成都',code:'D2',name:'业务部'}
  ],[{office:'O1056',code:'TY',name:'太原'},{office:'O1056',code:'CD',name:'成都'},{office:'O1056',code:'HZ',name:'杭州'}])
  expect(branchGroups.map(g=>g.name)).toEqual(['太原','成都','杭州'])
  expect(branchGroups[0].children.flatMap((d:any)=>d.members).map((m:any)=>m.id)).toEqual(['active','unassigned'])
  expect(branchGroups[1].children[0].members[0].id).toBe('historical')
  expect(branchGroups[2].children).toEqual([])
})

test('dictionary hierarchy, team department switching and branch search',async({page})=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
  const boot=await (await page.request.get('/api/bootstrap')).json()
  if(boot.setup_required)await page.request.post('/api/setup',{data:{username:'admin',name:'林知远',password:'Browser-test-2026',demo:true}})
  await page.request.post('/api/auth/login',{data:{username:'admin',password:'Browser-test-2026'}})
  for(const category of ['case_cause','case_cause_interface']){
    for(const [code,parent_code,name] of [['TREE-A','0','树形根节点'],['TREE-B','TREE-A','树形第二层'],['TREE-C','TREE-B','树形末级'],['TREE-ORPHAN','absent','独立缺失父级']]){
      expect((await page.request.post('/api/admin/metadata',{data:{category,code,parent_code,name}})).ok()).toBeTruthy()
    }
  }
  await page.goto('/#admin')
  await page.getByRole('button',{name:'元数据管理',exact:true}).click()
  for(const categoryName of ['案由','接口案由']){
    await page.locator('.metadata-categories').getByRole('button',{name:new RegExp('^'+categoryName+' ')}).click()
    const tree=page.getByRole('tree',{name:categoryName,exact:true})
    await expect(tree.locator('[data-code="TREE-A"]')).toBeVisible()
    await expect(tree.locator('[data-code="TREE-C"]')).not.toBeVisible()
    await tree.getByRole('button',{name:'展开树形第二层',exact:true}).click()
    await expect(tree.locator('[data-code="TREE-C"]')).toBeVisible()
    await tree.getByRole('button',{name:'收起树形根节点',exact:true}).click()
    await expect(tree.locator('[data-code="TREE-C"]')).not.toBeVisible()
    await page.getByLabel('搜索字典项').fill('树形末级')
    await expect(tree.locator('.metadata-tree-node')).toHaveCount(3)
    await expect(tree.locator('[data-code="TREE-C"]')).toBeVisible()
    await page.getByLabel('搜索字典项').fill('不存在的字典')
    await expect(page.getByText('此类别暂无匹配字典项，可新增或调整搜索条件')).toBeVisible()
    await page.getByLabel('搜索字典项').fill('')
  }
  await page.screenshot({path:'artifacts/screenshots/metadata-tree.png',fullPage:true})
  await page.getByRole('button',{name:'律师团队',exact:true}).click()
  const sidebar=page.getByLabel('部门树',{exact:true})
  await sidebar.getByRole('button',{name:/争议解决部/}).click()
  await expect(page.locator('.lawyer-team-members tbody tr')).toHaveCount(2)
  await expect(sidebar.getByRole('button',{name:/公司业务部/})).toBeVisible()
  await sidebar.getByRole('button',{name:/公司业务部/}).click()
  await expect(page.locator('.lawyer-team-members tbody tr')).toHaveCount(2)
  await expect(page.locator('.lawyer-team-members')).toContainText('陈以宁')
  await page.getByLabel('搜索律师团队').fill('沈亦舟')
  await expect(page.locator('.lawyer-team-members tbody tr')).toHaveCount(1)
  await page.locator('.lawyer-team-members').getByRole('button',{name:'查看',exact:true}).click()
  await expect(page.locator('.detail-drawer')).toContainText('沈亦舟')
  await page.getByLabel('关闭详情').click()
  await page.getByLabel('搜索律师团队').fill('')
  await page.screenshot({path:'artifacts/screenshots/lawyer-department-tree.png',fullPage:true})
  await page.setViewportSize({width:390,height:844})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBeTruthy()
  await page.setViewportSize({width:1440,height:1050})
  const lawyers=await (await page.request.get('/api/records/lawyers')).json()
  for(const number of ['CS-UI-001','CS-UI-002','HZ-UI-001']){
    expect((await page.request.post('/api/records/cases',{data:{number,title:'分支筛选验证',client:'分支委托人',office:'上海总部',lead_lawyer_id:lawyers.items[0].id,category:'民事诉讼',opened_on:'2026-11-13',amount:'0'}})).ok()).toBeTruthy()
  }
  await page.getByRole('button',{name:'案件管理',exact:true}).click()
  await page.getByLabel('搜索记录').fill('分支筛选验证')
  await page.getByLabel('筛选分支机构').selectOption('CS')
  await expect(page.locator('tbody tr')).toHaveCount(2)
  await expect(page.locator('tbody')).not.toContainText('HZ-UI-001')
  await page.getByLabel('筛选分支机构').selectOption('HZ')
  await expect(page.locator('tbody tr')).toHaveCount(1)
  await expect(page.locator('tbody')).toContainText('HZ-UI-001')
  const exported=await page.request.get('/api/export/cases?branch=HZ&q=分支筛选验证')
  expect(await exported.text()).toContain('HZ-UI-001')
  expect(await exported.text()).not.toContain('CS-UI-001')
  await page.screenshot({path:'artifacts/screenshots/case-branch-filter.png',fullPage:true})
  expect(errors).toEqual([])
})
