import React from 'react';
import {SbAmountInput} from '@shandiant/ui-react';

export function formatForecastAmount(value, info, editing = false) {
  if (info?.userTyping) return info.input ?? String(value ?? '');
  if (value === '' || value == null || !Number.isFinite(Number(value))) return '';
  const [integer, fraction] = Number(value).toFixed(editing ? 6 : 1).split('.');
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '.' + fraction;
}

// InputNumber parses its visible text again on blur and Enter. Show the full
// yuan/cents precision while editing so those commits cannot save the rounded
// one-decimal summary back into the shared forecast form.
export default class ForecastAmountInput extends React.Component {
  state = {editing: false};

  render() {
    const raw = this.props.value;
    const value = raw === '' || raw == null || !Number.isFinite(Number(raw)) ? null : Number(raw);
    return <SbAmountInput {...this.props} value={value} precision={6}
      formatter={(value, info) => formatForecastAmount(value, info, this.state.editing)}
      onFocus={() => this.setState({editing: true})}
      onBlur={() => this.setState({editing: false})} />;
  }
}
