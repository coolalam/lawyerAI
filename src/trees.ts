export interface TreeNode { id:string; code:string; name:string; children:TreeNode[]; [key:string]:any }

// Parent codes are scoped to one dictionary. Never infer ancestry from code prefixes.
// Orphans and cycles remain visible instead of disappearing or recursing forever.
export function metadataForest(items:any[]):TreeNode[] {
  const nodes=new Map<string,TreeNode>(items.map(item=>[item.code,{...item,children:[]}]))
  const roots:TreeNode[]=[]
  for(const node of nodes.values()) {
    const parent=nodes.get(node.parent_code)
    let current=parent, cyclic=false
    const visited=new Set<string>([node.code])
    while(current){if(visited.has(current.code)){cyclic=true;break}visited.add(current.code);current=nodes.get(current.parent_code)}
    if(parent&&!cyclic)parent.children.push(node)
    else { if(cyclic)node.tree_note='父级关系循环，暂列根节点'; else if(node.parent_code&&!['0','-1'].includes(node.parent_code))node.tree_note='父级未找到：'+node.parent_code; roots.push(node) }
  }
  return roots
}

export function departmentKey(row:any):string {
  return JSON.stringify([row.office||'',row.branch_code||'',row.department_code? 'code:'+row.department_code : row.department? 'name:'+row.department : 'unassigned'])
}

export function organizationGroups(members:any[],departments:any[]=[],branches:any[]=[]) {
  const offices=new Map<string,any>(), groups=new Map<string,any>()
  function officeNode(row:any){
    const office=row.office||'',branch=row.branch_code||'',key=JSON.stringify(['office',office,branch])
    const known=branches.find(b=>b.office===office&&b.code===branch)
    const name=known?.name||row.branch_name||branch||(branches.some(b=>b.office===office)?'未分配分支机构':office)||'未分配分支机构'
    if(!offices.has(key))offices.set(key,{key,name,office,branch_code:branch,children:[]})
    return offices.get(key)
  }
  function add(row:any,key:string,name:string,code:string,department?:any){
    const office=officeNode(row)
    if(!groups.has(key)){const group={key,name,code,office:row.office,branch_code:row.branch_code||'',branch_name:office.name,department,members:[]};groups.set(key,group);office.children.push(group)}
    return groups.get(key)
  }
  for(const branch of branches)officeNode({...branch,branch_code:branch.code,branch_name:branch.name})
  for(const d of departments)add(d,departmentKey({...d,department_code:d.code}),d.name,d.code,d)
  for(const member of members){
    const office=member.office||''
    // A single exact office/name match is safe for local records that predate managed codes.
    const matches=departments.filter(d=>d.office===office&&(d.branch_code||'')===(member.branch_code||'')&&d.name===member.department)
    const d=!member.department_code&&matches.length===1?matches[0]:null
    const key=d?departmentKey({...member,department_code:d.code}):departmentKey(member)
    add(member,key,member.department||'未分配部门',member.department_code||'').members.push(member)
  }
  return Array.from(offices.values())
}
