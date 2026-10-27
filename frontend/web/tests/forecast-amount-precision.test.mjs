import test from 'node:test';
import assert from 'node:assert/strict';
import Module, {createRequire} from 'node:module';
import fs from 'node:fs';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {buildSync} from 'esbuild';

const require = createRequire(import.meta.url), root = fileURLToPath(new URL('../', import.meta.url));
const {SbAmountInput} = require('@shandiant/ui-react');
const decimal = require('@rc-component/mini-decimal');
const compiled = buildSync({absWorkingDir:root, entryPoints:['department-ui/OpportunityCreate.jsx'], bundle:true,
  write:false, platform:'node', format:'cjs', packages:'external', loader:{'.css':'empty'}}).outputFiles[0].text;
const m = new Module(root+'forecast-precision-test.cjs'); m.filename=root+'forecast-precision-test.cjs'; m.paths=Module._nodeModulePaths(root); m._compile(compiled,m.filename);
const nodes = tree => !tree || typeof tree !== 'object' ? [] : [tree,...React.Children.toArray(tree.props?.children).flatMap(nodes)];
function fixture(collection, recognized, disabled=false) {
  const form={data:{showForecast:true,collection,recognized}}, calls=[];
  const tree=m.exports.QuarterForecast({form,disabled,invokeOn:(target,name,payload)=>{
    calls.push({target,name,payload}); form.data[payload.dataset.key]=payload.detail.value;
  }});
  const controls=nodes(tree).filter(n=>n.type?.name==='ForecastAmountInput').map(n=>{
    const instance=new n.type(n.props);
    // Drive the component's actual focus state without a DOM. The installed
    // InputNumber parsing/commit code below remains unchanged.
    instance.setState=update=>{instance.state={...instance.state,...update};};
    return instance;
  });
  assert.equal(controls.length,2);
  return {form,calls,controls,get inputs(){return controls.map(c=>c.render());}};
}

// Execute the installed input-number's actual commit/rounding function, with
// the real SbAmountInput props. No browser, API, database or rewritten math.
const rc = fs.readFileSync(root+'node_modules/@rc-component/input-number/es/InputNumber.js','utf8');
const decimalValueCode=rc.slice(rc.indexOf('const getDecimalValue ='),rc.indexOf('const getDecimalIfValidate ='));
const precisionCode=rc.slice(rc.indexOf('  const getPrecision ='),rc.indexOf('  // >>> Parser'));
const commitCode=rc.slice(rc.indexOf('  const triggerValueUpdate ='),rc.indexOf('  // ========================== User Input'));
function commit(input, text, userTyping=false) {
  const props=SbAmountInput(input.props).props;
  const context=vm.createContext({React:{useCallback:fn=>fn},getMiniDecimal:decimal.default,toFixed:decimal.toFixed,
    getNumberPrecision:decimal.getNumberPrecision,precision:props.precision,step:1,readOnly:false,disabled:props.disabled,
    value:props.value,decimalValue:decimal.default(props.value),stringMode:props.stringMode,onChange:props.onChange,
    isInRange:d=>d.isEmpty()||d.toNumber()>=0,getRangeValue:()=>null,setUncontrolledDecimalValue(){},setInputValue(){}});
  vm.runInContext(decimalValueCode+precisionCode+commitCode+';globalThis.commit=triggerValueUpdate;',context);
  return context.commit(decimal.default(props.parser(text)),userTyping);
}

test('forecast summaries display one decimal and correctly group only the integer part',()=>{
  for(const [value,expected] of [['0.007','0.0'],['0.008','0.0'],['0.000001','0.0'],['30','30.0'],['1234.567891','1,234.6']]) {
    const f=fixture(value,value);
    for(const input of f.inputs){
      const html=renderToStaticMarkup(input),props=SbAmountInput(input.props).props;
      assert.ok(html.includes(`value="${expected}"`),html);
      assert.equal(props.precision,6,'storage precision is still one cent in wan units');
      assert.equal(props.formatter(Number(value),{userTyping:false}),expected);
      assert.equal(props.formatter(value,{userTyping:true,input:value}),value);
    }
    assert.equal(f.calls.length,0,'opening the editor must not change forecasts');
  }
});

test('focus, Enter, blur and untouched save preserve the original forecast, including sub-display precision',()=>{
  for(const value of ['0.007','0.008000','0.000001','1234.567891']) {
    const f=fixture(value,value);
    for(const c of f.controls) {
      c.render().props.onFocus();
      const focused=c.render(),props=SbAmountInput(focused.props).props;
      const text=props.formatter(value,{userTyping:false});
      assert.equal(Number(props.parser(text)),Number(value),'editing exposes the exact stored amount');
      assert.equal(text.split('.')[1].length,6);
      commit(focused,text);
      focused.props.onBlur();
      commit(focused,text);
      assert.equal(SbAmountInput(c.render().props).props.formatter(value,{userTyping:false}),Number(value).toLocaleString('en-US',{minimumFractionDigits:1,maximumFractionDigits:1}));
    }
    assert.equal(f.calls.length,0);
    assert.equal(f.form.data.collection,value);
    assert.equal(f.form.data.recognized,value);
  }
});

test('actual user edits commit the intended amount, including the previously rounded display value',()=>{
  for(const text of ['0.007','0.008000','0.000001','0.0','1,234.567891','1,234.6'])for(const index of [0,1]) {
    const f=fixture('1234.567890','0.009'),key=index===0?'collection':'recognized',other=index===0?'recognized':'collection';
    const beforeOther=f.form.data[other];
    f.controls[index].render().props.onFocus();
    commit(f.inputs[index],text,true);
    const expected=Number(text.replaceAll(',',''));
    assert.equal(f.calls.length,1);
    assert.equal(f.calls[0].target,f.form);assert.equal(f.calls[0].name,'forecast');
    assert.equal(f.form.data[key],String(expected));assert.equal(f.form.data[other],beforeOther);
    assert.equal(Number(f.form.data[key])*10000,expected*10000);
  }
});

test('real amount controls preserve missing versus zero and disabled forecasts cannot commit',()=>{
  for(const missing of ['',null,undefined]) {
    const f=fixture(missing,'0');
    assert.ok(renderToStaticMarkup(f.inputs[0]).includes('value=""'));
    assert.ok(renderToStaticMarkup(f.inputs[1]).includes('value="0.0"'));
    f.controls[0].render().props.onFocus();commit(f.inputs[0],'');
    assert.equal(f.calls.length,0);
  }
  const f=fixture('0.007','0.008');commit(f.inputs[0],'',true);commit(f.inputs[1],'0',true);
  assert.equal(f.form.data.collection,'');assert.equal(f.form.data.recognized,'0');
  const blocked=fixture('0.007','0.008',true);commit(blocked.inputs[0],'0.000001',true);assert.equal(blocked.calls.length,0);
});
