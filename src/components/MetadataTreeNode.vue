<script setup lang="ts">
import { computed } from 'vue'
import { ChevronDown, ChevronRight, Folder, FileText } from 'lucide-vue-next'
import StatusBadge from './StatusBadge.vue'
import type { TreeNode } from '../trees'
const props=withDefaults(defineProps<{node:TreeNode;expanded:Record<string,boolean>;level?:number;searching?:boolean}>(),{level:0,searching:false})
const emit=defineEmits(['toggle','edit'])
const open=computed(()=>props.searching||props.expanded[props.node.id]===true||(props.level===0&&props.expanded[props.node.id]!==false))
</script>
<template>
  <div class="tree-branch" role="treeitem" :aria-label="node.name+'（'+node.code+'）'" :aria-level="level+1" :aria-expanded="node.children.length?open:undefined">
    <div class="tree-node metadata-tree-node" :data-code="node.code">
      <button v-if="node.children.length" class="tree-toggle-button" :aria-label="(open?'收起':'展开')+node.name" :aria-expanded="open" @click="emit('toggle',{id:node.id,open:!open})"><component :is="open?ChevronDown:ChevronRight" :size="16"/></button><span v-else class="tree-spacer"></span>
      <component :is="node.children.length?Folder:FileText" :size="16" class="tree-folder"/>
      <span class="tree-label"><strong>{{ node.name }}</strong><code>{{ node.code }}</code><small v-if="node.tree_note">{{ node.tree_note }}</small></span>
      <span class="tree-meta">{{ node.children?.length ? `${node.children.length} 个下级` : '' }}</span>
      <StatusBadge :status="node.active?'active':'void'" :label="node.active?'启用':'停用'"/>
      <button class="text-button" @click="emit('edit',node)">编辑</button>
    </div>
    <div v-if="open && node.children?.length" class="tree-children" role="group">
      <MetadataTreeNode v-for="child in node.children" :key="child.id" :node="child" :expanded="expanded" :level="level+1" :searching="searching" @toggle="emit('toggle',$event)" @edit="emit('edit',$event)"/>
    </div>
  </div>
</template>
