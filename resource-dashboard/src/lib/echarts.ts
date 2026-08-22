import * as echarts from 'echarts/core'
import { BarChart, EffectScatterChart, LineChart, MapChart, ScatterChart } from 'echarts/charts'
import { GeoComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  BarChart,
  EffectScatterChart,
  LineChart,
  MapChart,
  ScatterChart,
  GeoComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
  CanvasRenderer,
])

export { echarts }
