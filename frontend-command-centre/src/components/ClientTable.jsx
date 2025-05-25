import React from 'react';
import { ChevronDown, ChevronUp, Smartphone, HardDrive, Cpu, MemoryStick, Wifi, AlertCircle, CheckCircle2, XCircle, PowerOff } from 'lucide-react';

const getStatusClasses = (status) => {
  switch (status) {
    case 'active':
      return 'bg-green-500/20 text-green-400';
    case 'inactive':
      return 'bg-slate-600/30 text-slate-400';
    case 'error':
      return 'bg-red-500/20 text-red-400';
    default:
      return 'bg-yellow-500/20 text-yellow-400';
  }
};

const getStatusIcon = (status) => {
    switch (status) {
        case 'active': return <CheckCircle2 size={18} className="mr-2" />;
        case 'inactive': return <PowerOff size={18} className="mr-2 text-slate-500" />;
        case 'error': return <XCircle size={18} className="mr-2" />;
        default: return <AlertCircle size={18} className="mr-2" />;
    }
};

const ClientRow = ({ client }) => {
  const [expanded, setExpanded] = React.useState(false);

  return (
    <>
      <tr 
        className="border-b border-slate-700 hover:bg-slate-700/50 transition-colors duration-150 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
        tabIndex={0}
        aria-expanded={expanded}
        aria-label={`Client ${client.name}, status ${client.status}. Click to ${expanded ? 'collapse' : 'expand'} details.`}
        onKeyDown={(e) => e.key === 'Enter' && setExpanded(!expanded)}
        >
        <td className="px-4 py-3 whitespace-nowrap">
          <div className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${getStatusClasses(client.status)}`}>
            {getStatusIcon(client.status)}
            {client.status.charAt(0).toUpperCase() + client.status.slice(1)}
          </div>
        </td>
        <td className="px-4 py-3 font-medium text-purple-300 whitespace-nowrap">{client.name}</td>
        <td className="px-4 py-3 text-slate-400 whitespace-nowrap">{client.type}</td>
        <td className="px-4 py-3 text-sky-400 whitespace-nowrap">{client.ip}</td>
        <td className="px-4 py-3 text-slate-400 whitespace-nowrap">{client.lastSeen}</td>
        <td className="px-4 py-3 text-slate-300 whitespace-nowrap">
            {expanded ? <ChevronUp size={20} /> : <ChevronDown size={20} />}
        </td>
      </tr>
      {expanded && (
        <tr className="bg-slate-750">
          <td colSpan={6} className="p-0">
            <div className="p-4 grid grid-cols-1 md:grid-cols-3 gap-4 bg-slate-700/30">
              <div className="flex items-center space-x-2 p-3 bg-slate-800 rounded-md">
                <Cpu size={20} className="text-teal-400" />
                <div>
                  <p className="text-xs text-slate-400">CPU Usage</p>
                  <p className="text-sm font-semibold text-teal-300">{client.cpu}</p>
                </div>
              </div>
              <div className="flex items-center space-x-2 p-3 bg-slate-800 rounded-md">
                <MemoryStick size={20} className="text-orange-400" />
                <div>
                  <p className="text-xs text-slate-400">Memory</p>
                  <p className="text-sm font-semibold text-orange-300">{client.memory}</p>
                </div>
              </div>
              <div className="flex items-center space-x-2 p-3 bg-slate-800 rounded-md">
                <HardDrive size={20} className="text-indigo-400" />
                <div>
                  <p className="text-xs text-slate-400">Disk Space</p>
                  <p className="text-sm font-semibold text-indigo-300">{client.disk}</p>
                </div>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
};

const ClientTable = ({ clients }) => {
  if (!clients || clients.length === 0) {
    return <p className="text-center text-slate-400 py-8">No client data available.</p>;
  }

  return (
    <div className="bg-slate-800 p-6 rounded-xl shadow-xl flex-grow flex flex-col min-h-[400px]">
      <h2 className="text-2xl font-semibold text-purple-400 mb-6">Registered Clients</h2>
      <div className="overflow-x-auto flex-grow rounded-lg scrollbar-thin scrollbar-thumb-slate-600 scrollbar-track-slate-700">
        <table className="min-w-full divide-y divide-slate-700">
          <thead className="bg-slate-700/50 sticky top-0 z-10">
            <tr>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Status</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Name</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Type</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">IP Address</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Last Seen</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider"></th>{/* For expand icon */}
            </tr>
          </thead>
          <tbody className="bg-slate-800 divide-y divide-slate-700">
            {clients.map((client) => (
              <ClientRow key={client.id} client={client} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default ClientTable; 