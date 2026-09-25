import React from 'react';
import {SbSegmented} from '@shandiant/ui-react';
export default function PresentationSwitch({data, invoke}) {
  if (!(data.presentationOptions?.length > 1)) return null;
  return <div className="ds-sync-perspective" aria-label="业务视角"><SbSegmented value={data.presentation} options={data.presentationOptions} onChange={kind => invoke('changePresentation', {dataset: {kind}})}/></div>;
}
