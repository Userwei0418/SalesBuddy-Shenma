import React,{useMemo,useState} from 'react';
import {Alert,Button} from 'antd';
import {open} from '../pc/data.js';
import MeetingBoardView from './MeetingBoardView.jsx';
import {meetingView,overviewView} from './meeting-presentation.js';
import {withTaskActivity} from './task-activity.js';

export default function MeetingFactsBoard({facts,title,actions,presenting=false,hideMeetingButton=false,showTaskActivity=false,taskActivity}) {
  const [member,setMember]=useState(null);
  const adapter=facts?.contract_version==='work_report.overview_facts.v1'?overviewView:meetingView;
  const view=(memberId,heading)=>{
    const value=adapter(facts,{memberId,title:heading});
    return showTaskActivity?withTaskActivity(value,taskActivity,{memberId,dataAsOf:facts.data_as_of}):value;
  };
  const model=useMemo(()=>{try{return {value:view(member,member?undefined:title)};}catch(error){return {error:error.message};}},[facts,member,title,showTaskActivity,taskActivity]);
  if(model.error)return <Alert type="error" title={model.error} action={member && <Button onClick={()=>setMember(null)}>返回团队</Button>}/>;
  const meetingMembers=()=>model.value.members.map(row=>{
    try{return {...row,value:view(row.id)};}catch(error){return {...row,error:error.message};}
  });
  return <MeetingBoardView value={model.value} presenting={presenting} hideMeetingButton={hideMeetingButton} onMember={row=>setMember(row.id)} onBack={member?()=>setMember(null):undefined}
    meetingMembers={meetingMembers}
    onOpenOpportunity={row=>open.opportunity(row.customerId,row.opportunityId)} onOpenCustomer={row=>open.customer(row.customerId)}
    onOpenTask={row=>open.task(row.taskId)}
    actions={member?undefined:actions}/>;
}
