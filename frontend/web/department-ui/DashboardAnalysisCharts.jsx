import React,{useEffect,useRef} from 'react';
import * as echarts from 'echarts/core';
import {BarChart,CustomChart,PieChart} from 'echarts/charts';
import {AriaComponent,GridComponent,LegendComponent,TooltipComponent} from 'echarts/components';
import {SVGRenderer} from 'echarts/renderers';
import theme from '@shandiant/tokens/bridge-echarts';
import {PIPELINE_METRICS} from './dashboard-pipeline-activity.js';

let registered=false;
function chartEngine() {
  if(!registered){echarts.use([BarChart,CustomChart,PieChart,AriaComponent,GridComponent,LegendComponent,TooltipComponent,SVGRenderer]);registered=true;}
  return echarts;
}

export function DashboardAnalysisChart({option,label,height=300,onSelect}) {
  const host=useRef(null),instance=useRef(null),handler=useRef(onSelect);handler.current=onSelect;
  useEffect(()=>{
    const element=host.current;if(!element)return;
    const chart=chartEngine().init(element,theme,{renderer:'svg'});instance.current=chart;
    chart.on('click',event=>handler.current?.(event.data?.code));
    const resize=()=>{if(element.isConnected)chart.resize();};
    const observer=typeof ResizeObserver==='undefined'?null:new ResizeObserver(resize);
    observer?.observe(element);if(!observer)window.addEventListener('resize',resize);
    return ()=>{observer?.disconnect();if(!observer)window.removeEventListener('resize',resize);chart.dispose();instance.current=null;};
  },[]);
  useEffect(()=>{instance.current?.setOption({...option,animation:false,aria:{enabled:true,...option.aria}},{notMerge:true});},[option]);
  return <div className="ds-analysis-chart" ref={host} role="img" aria-label={label} style={{height}}/>;
}

// Native ECharts 6.1 funnel polygons ignore borderRadius. This bounded path
// rounds the final coordinates so narrowing never distorts corners or hatching.
function roundedFunnelPath(points,radius=6) {
  const vertices=points.filter((point,index)=>!index||point[0]!==points[index-1][0]||point[1]!==points[index-1][1]);
  if(vertices.length<3)return '';
  const corners=vertices.map((point,index)=>{
    const previous=vertices[(index+vertices.length-1)%vertices.length],next=vertices[(index+1)%vertices.length];
    const before=Math.hypot(previous[0]-point[0],previous[1]-point[1]),after=Math.hypot(next[0]-point[0],next[1]-point[1]);
    const inset=Math.min(radius,before/2,after/2);
    const along=(other,length)=>point.map((value,axis)=>value+(other[axis]-value)*inset/length);
    return {point,enter:along(previous,before),exit:along(next,after)};
  });
  return `M${corners[0].enter.join(',')} `+corners.map((corner,index)=>`${index?'L'+corner.enter.join(',')+' ':''}Q${corner.point.join(',')} ${corner.exit.join(',')}`).join(' ')+' Z';
}

export function funnelOption(stages,selected='') {
  const bands=Math.max(1,stages.length),max=Math.max(0,...stages.map(row=>row.count));
  return {aria:{label:{description:'PPL 阶段数量图，各层高度固定，仅宽度随相对数量变化；少量数据保留最小可见宽度，斜线空心轮廓表示零。各层基准不同，准确数量以标签为准，不表示转化率，可用阶段按钮筛选。'}},tooltip:{trigger:'item',confine:true,renderMode:'richText',formatter:item=>`${item.name}：${item.value} 个`},
    series:[{name:'PPL 阶段示意',type:'custom',coordinateSystem:'none',dimensions:['count'],encode:{value:0,tooltip:0},
      renderItem:(params,api)=>{
        const index=params.dataIndex,row=stages[index],gap=8,top=12;
        const plotWidth=Math.max(0,api.getWidth()-152),center=8+plotWidth/2;
        const height=Math.max(0,(api.getHeight()-top-18-gap*(stages.length-1))/stages.length),y=top+index*(height+gap);
        const baseWidth=plotWidth*(1-0.7*index/bands),baseNextWidth=plotWidth*(1-0.7*(index+1)/bands);
        const children=[],opacity=!selected||selected===row.code?1:0.3;
        if(baseWidth>0&&height>0){
          // Only width follows count. Zero and tiny positives share a visible
          // minimum; layer height, vertical position and label remain fixed.
          const ratio=Math.max(Math.min(1,28/baseWidth),max?row.count/max:0);
          const width=baseWidth*ratio,nextWidth=baseNextWidth*ratio;
          const pathData=roundedFunnelPath([[-width/2,-height/2],[width/2,-height/2],[nextWidth/2,height/2],[-nextWidth/2,height/2]],Math.min(6,height/4));
          const empty=row.count===0;
          const shapes=[{type:'path',shape:{pathData},style:{fill:empty?'transparent':theme.color[index%theme.color.length],
            ...(empty?{stroke:theme.color[4],lineWidth:1,strokeNoScale:true}:{}),opacity},emphasis:{style:{opacity:1}}}];
          if(empty){
            const lines=[];
            for(let x=-width/2-height;x<width/2;x+=6)lines.push({type:'line',silent:true,
              shape:{x1:x,y1:height/2,x2:x+height,y2:-height/2},style:{stroke:theme.color[4],lineWidth:1,strokeNoScale:true,opacity:0.45*opacity}});
            shapes.push({type:'group',silent:true,clipPath:{type:'path',shape:{pathData}},children:lines});
          }
          children.push({type:'group',x:center,y:y+height/2,children:shapes});
        }
        // Counts (including zero) remain explicit, with the same clickable item code.
        children.push({type:'text',style:{x:plotWidth+22,y:y+height/2,text:`${row.label}\n${row.count} 个`,fill:theme.textStyle.color,
          fontFamily:theme.textStyle.fontFamily,fontSize:13,lineHeight:18,fontWeight:selected===row.code?650:400,verticalAlign:'middle'}});
        return {type:'group',children};
      },
      data:stages.map(row=>({name:row.label,value:row.count,code:row.code}))}]};
}

export function stageBarsOption(rows) {
  return {color:[theme.color[0],theme.color[4]],
    legend:{top:0,data:PIPELINE_METRICS.map(row=>row.label)},
    grid:{left:100,right:42,top:48,bottom:30,containLabel:true},
    xAxis:{type:'value',name:'万元',axisLabel:{formatter:value=>Number(value).toLocaleString('zh-CN',{maximumFractionDigits:2})}},
    yAxis:{type:'category',inverse:true,data:rows.map(row=>row.label)},
    tooltip:{trigger:'axis',confine:true,renderMode:'richText',axisPointer:{type:'shadow'},formatter:params=>{
      const row=rows[params[0]?.dataIndex];if(!row)return '';
      return [row.label,...PIPELINE_METRICS.map(metric=>{const view=row.amounts[metric.code];return `${metric.label}：${view.text}${view.amount!=null?' 万元':''}${view.exact?`（${view.exact}）`:''}${view.note?'\n'+view.note:''}`;})].join('\n');
    }},
    series:PIPELINE_METRICS.map(metric=>({name:metric.label,type:'bar',barMaxWidth:18,
      data:rows.map(row=>row.amounts[metric.code].value),
      label:{show:rows.length===1,position:'right',formatter:item=>rows[item.dataIndex].amounts[metric.code].text},
    }))};
}

export function activityDonutOption(parts) {
  return {color:[theme.color[0],theme.color[4],'#9AA3B2'],
    tooltip:{trigger:'item',confine:true,renderMode:'richText',formatter:item=>`${item.name}：${item.value} 个`},
    series:[{type:'pie',radius:['62%','84%'],center:['50%','50%'],avoidLabelOverlap:true,
      stillShowZeroSum:false,label:{show:false},emphasis:{scale:false},data:parts.map(row=>({name:row.label,value:row.count}))}]};
}
