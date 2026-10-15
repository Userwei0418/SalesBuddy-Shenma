import React,{useState} from 'react';
import {useListPageSize} from '../pc/list-pagination.js';
import {useAsync} from '../pc/ui.jsx';
import ReportsList from './ReportsList.jsx';
import {reportRow} from './presentation.js';

export default function ReportDocuments({owner,service,onOpen,onCreate}) {
  const [pageSize,setPageSize]=useListPageSize('reports');
  const [query,setQuery]=useState({text:'',status:''}),[committed,setCommitted]=useState({q:'',status:'',page:1});
  const listed=useAsync(()=>service.documents({q:committed.q,status:committed.status,limit:pageSize,offset:(committed.page-1)*pageSize}),[owner,service,committed.q,committed.status,committed.page,pageSize]);
  const change=value=>{setQuery(value);if(value.status!==query.status)setCommitted({q:value.text.trim(),status:value.status,page:1});};
  return <ReportsList items={(listed.value?.items || []).map(reportRow)} loading={listed.loading} error={listed.error} onRetry={listed.reload} onOpen={onOpen} onCreate={onCreate}
    query={query} onQuery={change} onSearch={text=>{setQuery(previous=>({...previous,text}));setCommitted({q:text.trim(),status:query.status,page:1});}}
    statuses={[{value:'',label:'全部状态'},{value:'private',label:'个人草稿'},{value:'shared',label:'已共享'}]}
    pagination={{page:committed.page,pageSize,total:listed.value?.total || 0}} onPage={(page,size)=>{setPageSize(size);setCommitted(previous=>({...previous,page:size!==pageSize?1:page}));}}/>;
}
