// Plot persisted observations only; missing scores are gaps, not zeros.
function draw(ctx,width,height,points){
  points=points.map(p=>({...p,time:Date.parse(p.date)})).filter(p=>Number.isFinite(p.time)).sort((a,b)=>a.time-b.time);
  const left=34,right=16,top=18,bottom=30,w=width-left-right,h=height-top-bottom;
  ctx.clearRect(0,0,width,height);ctx.setFontSize(10);ctx.setTextAlign('right');
  [0,50,100].forEach(n=>{const y=top+h*(1-n/100);ctx.setStrokeStyle('#D5DCE7');ctx.setLineWidth(1);ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(width-right,y);ctx.stroke();ctx.setFillStyle('#56677E');ctx.fillText(String(n),left-6,y+3);});
  const first=points.length?points[0].time:0,last=points.length?points[points.length-1].time:0;
  const valid=p=>p.score!==null&&Number.isFinite(p.score),x=i=>left+(last>first?(points[i].time-first)/(last-first):.5)*w,y=p=>top+h*(1-p.score/100);
  ctx.setStrokeStyle('#2863CD');ctx.setLineWidth(2);ctx.beginPath();let connected=false;
  let previousBasis=null;
  points.forEach((p,i)=>{const basis=p.score_basis&&p.score_basis.snapshot_status==='frozen'?p.score_basis.basis_id:null;if(!valid(p)){connected=false;previousBasis=null;return;}if(connected&&basis&&basis===previousBasis)ctx.lineTo(x(i),y(p));else ctx.moveTo(x(i),y(p));connected=true;previousBasis=basis;});ctx.stroke();
  points.forEach((p,i)=>{if(!valid(p))return;ctx.setFillStyle('#2863CD');ctx.beginPath();ctx.arc(x(i),y(p),3,0,Math.PI*2);ctx.fill();});
  if(points.length){ctx.setFillStyle('#56677E');ctx.setTextAlign('left');ctx.fillText(points[0].date.slice(5),left,height-8);ctx.setTextAlign('right');ctx.fillText(points[points.length-1].date.slice(5),width-right,height-8);}
  ctx.draw();
}
module.exports={draw};
