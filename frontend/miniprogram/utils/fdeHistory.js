// Plot persisted observations only; missing scores are gaps, not zeros.
function draw(ctx,width,height,points){
  const left=34,right=16,top=18,bottom=30,w=width-left-right,h=height-top-bottom;
  ctx.clearRect(0,0,width,height);ctx.setFontSize(10);ctx.setTextAlign('right');
  [0,50,100].forEach(n=>{const y=top+h*(1-n/100);ctx.setStrokeStyle('#e6edf6');ctx.setLineWidth(1);ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(width-right,y);ctx.stroke();ctx.setFillStyle('#8ba0b7');ctx.fillText(String(n),left-6,y+3);});
  const valid=p=>p.score!==null&&Number.isFinite(p.score),x=i=>left+(points.length>1?i/(points.length-1):.5)*w,y=p=>top+h*(1-p.score/100);
  ctx.setStrokeStyle('#2588df');ctx.setLineWidth(2);ctx.beginPath();let connected=false;
  points.forEach((p,i)=>{if(!valid(p)){connected=false;return;}if(connected)ctx.lineTo(x(i),y(p));else ctx.moveTo(x(i),y(p));connected=true;});ctx.stroke();
  points.forEach((p,i)=>{if(!valid(p))return;ctx.setFillStyle('#2588df');ctx.beginPath();ctx.arc(x(i),y(p),3,0,Math.PI*2);ctx.fill();});
  if(points.length){ctx.setFillStyle('#8ba0b7');ctx.setTextAlign('left');ctx.fillText(points[0].date.slice(5),left,height-8);ctx.setTextAlign('right');ctx.fillText(points[points.length-1].date.slice(5),width-right,height-8);}
  ctx.draw();
}
module.exports={draw};
